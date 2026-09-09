from datetime import UTC, datetime
from uuid import UUID

from backend.app.audit.service import AuditService
from backend.app.files.storage import ObjectStorage
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.data_lifecycle_action_archive import RecoveryArchiveExportActionMixin
from backend.app.workspaces.data_lifecycle_action_integrity import (
    RecoveryArchiveIntegrityActionMixin,
)
from backend.app.workspaces.data_lifecycle_action_restore import RecoveryRestoreDrillActionMixin
from backend.app.workspaces.data_lifecycle_constants import RECOVERY_READINESS_APPLY_ACTIONS
from backend.app.workspaces.data_lifecycle_recovery import _recovery_readiness_actions
from backend.app.workspaces.data_lifecycle_settings import (
    _backup_settings,
    _restore_drill_settings,
    _string_list,
)
from backend.app.workspaces.models import Workspace


class WorkspaceRecoveryActionService(
    RecoveryArchiveExportActionMixin,
    RecoveryRestoreDrillActionMixin,
    RecoveryArchiveIntegrityActionMixin,
):
    def apply_recovery_readiness_actions(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        queue: RedisQueue,
        storage: ObjectStorage | None = None,
        dry_run: bool = True,
        actions: list[str] | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
        readiness: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None or readiness is None:
            return None

        restore_readiness = readiness["restore_readiness"]
        if not isinstance(restore_readiness, dict):
            restore_readiness = {}
        recommended_actions = _string_list(restore_readiness.get("recommended_actions"))
        requested_actions = _recovery_readiness_actions(
            actions,
            recommended_actions=recommended_actions,
        )
        unsupported_actions = sorted(set(requested_actions) - RECOVERY_READINESS_APPLY_ACTIONS)
        if unsupported_actions:
            raise ValueError(f"Unsupported recovery readiness action: {unsupported_actions[0]}")

        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        blocked_reasons = _string_list(restore_readiness.get("blocked_reasons"))
        warnings = _string_list(restore_readiness.get("warnings"))
        metadata_keys = sorted((metadata or {}).keys())
        raw_backup_policy = _backup_settings(workspace.settings)
        raw_restore_drill_policy = _restore_drill_settings(workspace.settings)

        for action in requested_actions:
            if action == "run_archive_export":
                result, skipped_result = self._apply_recovery_archive_export_action(
                    workspace=workspace,
                    user_id=user_id,
                    queue=queue,
                    dry_run=dry_run,
                    reason=reason,
                    metadata_keys=metadata_keys,
                    blocked_reasons=blocked_reasons,
                    warnings=warnings,
                    raw_backup_policy=raw_backup_policy,
                )
            elif action == "run_restore_import_test":
                result, skipped_result = self._apply_recovery_restore_test_action(
                    workspace=workspace,
                    user_id=user_id,
                    storage=storage,
                    dry_run=dry_run,
                    reason=reason,
                    metadata_keys=metadata_keys,
                    blocked_reasons=blocked_reasons,
                    warnings=warnings,
                    raw_restore_drill_policy=raw_restore_drill_policy,
                )
            elif action == "verify_latest_archive_integrity":
                result, skipped_result = self._apply_recovery_archive_integrity_action(
                    workspace=workspace,
                    user_id=user_id,
                    storage=storage,
                    dry_run=dry_run,
                    reason=reason,
                    metadata_keys=metadata_keys,
                    blocked_reasons=blocked_reasons,
                    warnings=warnings,
                )
            else:
                continue
            if result is not None:
                results.append(result)
            if skipped_result is not None:
                skipped.append(skipped_result)

        summary = {
            "archive_export_jobs_enqueued": sum(
                1
                for item in results
                if item["action"] == "run_archive_export" and item["status"] == "applied"
            ),
            "restore_import_tests_completed": sum(
                1
                for item in results
                if item["action"] == "run_restore_import_test" and item["status"] == "applied"
            ),
            "archive_integrity_checks_completed": sum(
                1
                for item in results
                if item["action"] == "verify_latest_archive_integrity"
                and item["status"] == "applied"
            ),
            "active_archive_export_job_count": self._repo.active_archive_export_job_count(
                workspace_id
            ),
            "metadata_keys": metadata_keys,
            "readiness_blocked_reasons": blocked_reasons,
            "readiness_warnings": warnings,
            "unsupported_recommended_actions": sorted(
                set(recommended_actions) - RECOVERY_READINESS_APPLY_ACTIONS
            ),
        }
        if not dry_run:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=user_id,
                action="workspace.recovery_readiness.actions_applied",
                target_type="workspace",
                target_id=workspace_id,
                metadata={
                    "requested_actions": requested_actions,
                    "applied_count": len(results),
                    "skipped_count": len(skipped),
                    "summary": summary,
                    "reason": reason,
                },
            )
            self._session.commit()

        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "applied" if results else "noop",
            "requested_actions": requested_actions,
            "eligible_action_count": len(results),
            "applied_count": 0 if dry_run else len(results),
            "skipped_count": len(skipped),
            "summary": summary,
            "results": results,
            "skipped": skipped,
        }
