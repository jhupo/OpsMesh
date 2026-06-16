from uuid import UUID

from pydantic import ValidationError

from backend.app.api.services.exports import WorkspaceExportService
from backend.app.audit.service import AuditService
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.data_lifecycle_recovery import (
    _recovery_action_result,
    _recovery_action_skipped,
)
from backend.app.workspaces.data_lifecycle_schedule import _scheduled_archive_export_request
from backend.app.workspaces.models import Workspace


class RecoveryArchiveExportActionMixin:
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
