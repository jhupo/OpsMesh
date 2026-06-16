from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.workspaces.data_lifecycle_settings import _ensure_utc_datetime
from backend.app.workspaces.models import Workspace


class WorkspaceDataLifecycleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def has_active_archive_export_job(self, workspace_id: UUID) -> bool:
        return self.active_archive_export_job_count(workspace_id) > 0

    def active_archive_export_job_count(self, workspace_id: UUID) -> int:
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

    def latest_scheduled_archive_export_job(
        self,
        workspace_id: UUID,
    ) -> WorkspaceExportJob | None:
        jobs = self._session.scalars(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.export_type == "workspace_archive",
            )
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
            .limit(20)
        ).all()
        return next(
            (
                job
                for job in jobs
                if job.job_metadata.get("scheduled_by") == "workspace_data_lifecycle"
            ),
            None,
        )

    def latest_lifecycle_event(
        self,
        workspace_id: UUID,
        actions: tuple[str, ...],
    ) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action.in_(actions),
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

    def recent_lifecycle_events(
        self,
        workspace_id: UUID,
        actions: tuple[str, ...],
        limit: int = 10,
    ) -> list[AuditEvent]:
        return list(
            self._session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.action.in_(actions),
                )
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(limit)
            ).all()
        )


    def latest_lifecycle_retention_run_at(self, workspace_id: UUID) -> datetime | None:
        latest = self._session.scalar(
            select(func.max(AuditEvent.created_at)).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.retention_applied",
            )
        )
        return _ensure_utc_datetime(latest)

    def record_lifecycle_schedule_event(
        self,
        *,
        workspace: Workspace,
        action: str,
        reason: str,
        metadata: dict[str, object],
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=workspace.owner_user_id,
            action=action,
            target_type="workspace",
            target_id=workspace.id,
            metadata={"reason": reason, **metadata},
        )

    def latest_export_job(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob)
            .where(WorkspaceExportJob.workspace_id == workspace_id)
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
            .limit(1)
        )

    def latest_successful_archive_export(self, workspace_id: UUID) -> WorkspaceExportJob | None:
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

    def retention_export_job_candidates(
        self,
        *,
        workspace_id: UUID,
        cutoff: datetime,
        limit: int,
    ) -> list[WorkspaceExportJob]:
        if limit <= 0:
            return []
        return list(
            self._session.scalars(
                select(WorkspaceExportJob)
                .where(
                    WorkspaceExportJob.workspace_id == workspace_id,
                    WorkspaceExportJob.status.in_(
                        [
                            WorkspaceExportJobStatus.COMPLETED.value,
                            WorkspaceExportJobStatus.FAILED.value,
                        ]
                    ),
                    WorkspaceExportJob.created_at < cutoff,
                )
                .order_by(
                    WorkspaceExportJob.created_at.asc(),
                    WorkspaceExportJob.id.asc(),
                )
                .limit(limit)
            ).all()
        )

    def latest_failed_export_job(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.status == WorkspaceExportJobStatus.FAILED.value,
            )
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
            .limit(1)
        )

    def latest_archive_import_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_import.created",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

    def latest_archive_integrity_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_export_job.integrity_checked",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

    def latest_restore_drill_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_restore_drill.completed",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

