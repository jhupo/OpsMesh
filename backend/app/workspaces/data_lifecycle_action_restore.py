from uuid import UUID

from pydantic import ValidationError

from backend.app.api.services.exports import WorkspaceExportService
from backend.app.observability.audit_service import AuditService
from backend.app.files.storage import ObjectStorage
from backend.app.workspaces.data_lifecycle_recovery import (
    _recovery_action_result,
    _recovery_action_skipped,
)
from backend.app.workspaces.data_lifecycle_schedule import _scheduled_restore_drill_request
from backend.app.workspaces.data_lifecycle_store import LifecycleStore
from backend.app.workspaces.models import Workspace


class RecoveryRestoreDrillActionMixin(LifecycleStore):
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
        latest_success = self._repo.latest_successful_archive_export(workspace.id)
        if latest_success is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="no_successful_archive_export",
                blocked_reasons=blocked_reasons,
            )
        if self._repo.has_active_archive_export_job(workspace.id):
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
