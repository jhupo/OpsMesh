from uuid import UUID

from sqlalchemy import func, select

from backend.app.audit.models import AuditEvent
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus


class RecoveryActionQueryMixin:
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
