from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.services.exports import WorkspaceExportService
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.files.storage import ObjectStorage
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.data_lifecycle_constants import RECOVERY_READINESS_APPLY_ACTIONS
from backend.app.workspaces.data_lifecycle_payloads import _archive_integrity_payload
from backend.app.workspaces.data_lifecycle_recovery import (
    _recovery_action_result,
    _recovery_action_skipped,
    _recovery_readiness_actions,
)
from backend.app.workspaces.data_lifecycle_schedule import (
    _scheduled_archive_export_request,
    _scheduled_restore_drill_request,
)
from backend.app.workspaces.data_lifecycle_settings import (
    _backup_settings,
    _restore_drill_settings,
    _string_list,
)
from backend.app.workspaces.models import Workspace


class WorkspaceRecoveryActionService:
    def __init__(self, session: Session) -> None:
        self._session = session

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
        if workspace is None:
            return None

        if readiness is None:
            return None
        restore_readiness = readiness["restore_readiness"]
        if not isinstance(restore_readiness, dict):
            restore_readiness = {}
        recommended_actions = _string_list(restore_readiness.get("recommended_actions"))
        requested_actions = _recovery_readiness_actions(
            actions,
            recommended_actions=recommended_actions,
        )
        unsupported_actions = sorted(
            set(requested_actions) - RECOVERY_READINESS_APPLY_ACTIONS
        )
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
                result, skipped_result = (
                    self._apply_recovery_archive_integrity_action(
                        workspace=workspace,
                        user_id=user_id,
                        storage=storage,
                        dry_run=dry_run,
                        reason=reason,
                        metadata_keys=metadata_keys,
                        blocked_reasons=blocked_reasons,
                        warnings=warnings,
                    )
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
                if item["action"] == "run_restore_import_test"
                and item["status"] == "applied"
            ),
            "archive_integrity_checks_completed": sum(
                1
                for item in results
                if item["action"] == "verify_latest_archive_integrity"
                and item["status"] == "applied"
            ),
            "active_archive_export_job_count": self._active_archive_export_job_count(
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

    def _apply_recovery_archive_export_action(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        queue: RedisQueue,
        dry_run: bool,
        reason: str | None,
        metadata_keys: list[str],
        blocked_reasons: list[str],
        warnings: list[str],
        raw_backup_policy: dict[str, object],
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        action = "run_archive_export"
        if self._has_active_archive_export_job(workspace.id):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="archive_export_already_active",
                blocked_reasons=blocked_reasons,
            )
        try:
            request = _scheduled_archive_export_request(raw_backup_policy)
        except ValidationError:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="invalid_archive_request",
                blocked_reasons=blocked_reasons,
            )

        request_payload = request.model_dump(mode="json")
        if dry_run:
            return _recovery_action_result(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                status="would_apply",
                blocked_reasons=blocked_reasons,
                metadata={"request": request_payload, "readiness_warnings": warnings},
            ), None

        export_job = WorkspaceExportService(self._session).create_archive_export_job(
            workspace=workspace,
            user_id=user_id,
            request=request,
            queue=queue,
        )
        export_job.job_metadata = {
            **export_job.job_metadata,
            "source": "recovery_readiness_action",
            "reason": reason,
            "metadata_keys": metadata_keys,
            "readiness_blocked_reasons": blocked_reasons,
            "readiness_warnings": warnings,
        }
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.recovery_readiness.archive_export_enqueued",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={
                "reason": reason,
                "metadata_keys": metadata_keys,
                "readiness_blocked_reasons": blocked_reasons,
                "readiness_warnings": warnings,
            },
        )
        return _recovery_action_result(
            action=action,
            resource_type="workspace_export_job",
            resource_id=export_job.id,
            status="applied",
            blocked_reasons=blocked_reasons,
            metadata={"export_job_status": export_job.status, "request": request_payload},
        ), None

    def _apply_recovery_restore_test_action(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        storage: ObjectStorage | None,
        dry_run: bool,
        reason: str | None,
        metadata_keys: list[str],
        blocked_reasons: list[str],
        warnings: list[str],
        raw_restore_drill_policy: dict[str, object],
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        action = "run_restore_import_test"
        latest_success = self._latest_successful_archive_export(workspace.id)
        if latest_success is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="no_successful_archive_export",
                blocked_reasons=blocked_reasons,
            )
        if self._has_active_archive_export_job(workspace.id):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="archive_export_already_active",
                blocked_reasons=blocked_reasons,
            )
        if storage is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="storage_unavailable",
                blocked_reasons=blocked_reasons,
            )
        try:
            request = _scheduled_restore_drill_request(raw_restore_drill_policy)
        except ValidationError:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="invalid_restore_drill_request",
                blocked_reasons=blocked_reasons,
            )

        request_payload = request.model_dump(mode="json")
        if dry_run:
            return _recovery_action_result(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                status="would_apply",
                blocked_reasons=blocked_reasons,
                metadata={"request": request_payload, "readiness_warnings": warnings},
            ), None

        try:
            drill_result = WorkspaceExportService(self._session).run_archive_restore_drill(
                workspace=workspace,
                user_id=user_id,
                job_id=latest_success.id,
                request=request,
                storage=storage,
            )
        except (FileNotFoundError, ValueError):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="restore_drill_failed",
                blocked_reasons=blocked_reasons,
            )
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.recovery_readiness.restore_import_test_completed",
            target_type="workspace_export_job",
            target_id=latest_success.id,
            metadata={
                "reason": reason,
                "metadata_keys": metadata_keys,
                "readiness_blocked_reasons": blocked_reasons,
                "readiness_warnings": warnings,
                "passed": drill_result["passed"],
                "required_resolution_count": drill_result["required_resolution_count"],
                "suggested_resolution_count": drill_result["suggested_resolution_count"],
                "conflict_counts": drill_result["conflict_counts"],
            },
        )
        return _recovery_action_result(
            action=action,
            resource_type="workspace_export_job",
            resource_id=latest_success.id,
            status="applied",
            blocked_reasons=blocked_reasons,
            metadata={
                "passed": drill_result["passed"],
                "required_resolution_count": drill_result["required_resolution_count"],
                "suggested_resolution_count": drill_result["suggested_resolution_count"],
                "conflict_counts": drill_result["conflict_counts"],
                "request": request_payload,
            },
        ), None

    def _apply_recovery_archive_integrity_action(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        storage: ObjectStorage | None,
        dry_run: bool,
        reason: str | None,
        metadata_keys: list[str],
        blocked_reasons: list[str],
        warnings: list[str],
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        action = "verify_latest_archive_integrity"
        latest_success = self._latest_successful_archive_export(workspace.id)
        if latest_success is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="no_successful_archive_export",
                blocked_reasons=blocked_reasons,
            )

        latest_integrity = self._latest_archive_integrity_event(workspace.id)
        integrity_payload = _archive_integrity_payload(
            latest_integrity,
            latest_success=latest_success,
        )
        if (
            integrity_payload["latest_check_covers_latest_successful_archive"] is True
            and integrity_payload["latest_check_verified"] is True
        ):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="archive_integrity_already_verified",
                blocked_reasons=blocked_reasons,
            )
        if storage is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="storage_unavailable",
                blocked_reasons=blocked_reasons,
            )

        if dry_run:
            return _recovery_action_result(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                status="would_apply",
                blocked_reasons=blocked_reasons,
                metadata={
                    "readiness_warnings": warnings,
                    "latest_check_covers_latest_successful_archive": integrity_payload[
                        "latest_check_covers_latest_successful_archive"
                    ],
                },
            ), None

        try:
            verification = WorkspaceExportService(
                self._session
            ).verify_archive_export_job(
                workspace_id=workspace.id,
                job_id=latest_success.id,
                user_id=user_id,
                storage=storage,
            )
        except (FileNotFoundError, ValueError):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="archive_integrity_verification_failed",
                blocked_reasons=blocked_reasons,
            )

        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.recovery_readiness.archive_integrity_verified",
            target_type="workspace_export_job",
            target_id=latest_success.id,
            metadata={
                "reason": reason,
                "metadata_keys": metadata_keys,
                "readiness_blocked_reasons": blocked_reasons,
                "readiness_warnings": warnings,
                "verified": verification["verified"],
                "failed_checks": verification["failed_checks"],
            },
        )
        return _recovery_action_result(
            action=action,
            resource_type="workspace_export_job",
            resource_id=latest_success.id,
            status="applied",
            blocked_reasons=blocked_reasons,
            metadata={
                "verified": verification["verified"],
                "failed_checks": verification["failed_checks"],
                "checks": verification["checks"],
            },
        ), None



    def _has_active_archive_export_job(self, workspace_id: UUID) -> bool:
        return self._active_archive_export_job_count(workspace_id) > 0

    def _active_archive_export_job_count(self, workspace_id: UUID) -> int:
        active_count = self._session.scalar(
            select(func.count(WorkspaceExportJob.id)).where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.export_type == "workspace_archive",
                WorkspaceExportJob.status.in_(
                    [
                        WorkspaceExportJobStatus.QUEUED.value,
                        WorkspaceExportJobStatus.RUNNING.value,
                    ]
                ),
            )
        )
        return int(active_count or 0)

    def _latest_successful_archive_export(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.export_type == "workspace_archive",
                WorkspaceExportJob.status == WorkspaceExportJobStatus.COMPLETED.value,
            )
            .order_by(WorkspaceExportJob.completed_at.desc(), WorkspaceExportJob.id.desc())
            .limit(1)
        )

    def _latest_archive_integrity_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_export_job.integrity_checked",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )
