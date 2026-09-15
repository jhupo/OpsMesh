"""Scheduled workspace backup execution."""

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_lifecycle.repository import WorkspaceDataLifecycleRepository
from backend.app.domains.workspace.data_lifecycle.scheduling import (
    ScheduledLifecycleSummary,
    scheduled_archive_export_request,
    scheduled_backup_due,
    scheduled_lifecycle_detail,
)
from backend.app.domains.workspace.data_lifecycle.settings import _backup_settings
from backend.app.domains.workspace.data_transfer.service import WorkspaceExportService
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.runtime.workers.queue import RedisQueue


class ScheduledBackupService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = WorkspaceDataLifecycleRepository(session)

    def run_if_due(
        self,
        workspace: Workspace,
        queue: RedisQueue,
    ) -> ScheduledLifecycleSummary:
        raw_policy = _backup_settings(workspace.settings)
        latest_job = self._repository.latest_export_job(workspace.id)
        latest_success = self._repository.latest_successful_archive_export(workspace.id)
        from backend.app.domains.workspace.data_lifecycle.policy import _backup_policy

        backup_policy = _backup_policy(
            workspace.settings,
            latest_job,
            latest_success,
            generated_at=datetime.now(UTC),
        )
        schedule_status = backup_policy["schedule_status"]
        due = scheduled_backup_due(backup_policy)
        if not due:
            return ScheduledLifecycleSummary()

        if self._repository.has_active_archive_export_job(workspace.id):
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.backup_skipped",
                reason="archive_export_already_active",
                metadata={"schedule_status": schedule_status},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                backup_jobs_skipped=1,
                details=[
                    scheduled_lifecycle_detail(
                        workspace.id,
                        "backup",
                        "skipped",
                        "archive_export_already_active",
                    )
                ],
            )

        try:
            request = scheduled_archive_export_request(raw_policy)
        except ValidationError as exc:
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.backup_skipped",
                reason="invalid_archive_request",
                metadata={"error": str(exc)[:1000], "schedule_status": schedule_status},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                backup_jobs_skipped=1,
                details=[
                    scheduled_lifecycle_detail(
                        workspace.id,
                        "backup",
                        "skipped",
                        "invalid_archive_request",
                    )
                ],
            )

        export_job = WorkspaceExportService(self._session).create_archive_export_job(
            workspace=workspace,
            user_id=workspace.owner_user_id,
            request=request,
            queue=queue,
        )
        export_job.job_metadata = {
            **export_job.job_metadata,
            "scheduled_by": "workspace_data_lifecycle",
            "schedule_status": schedule_status,
        }
        self._repository.record_lifecycle_schedule_event(
            workspace=workspace,
            action="workspace.lifecycle.backup_enqueued",
            reason="backup_schedule_due",
            metadata={
                "export_job_id": str(export_job.id),
                "schedule_status": schedule_status,
            },
        )
        self._session.commit()
        return ScheduledLifecycleSummary(
            backup_jobs_enqueued=1,
            details=[
                scheduled_lifecycle_detail(
                    workspace.id,
                    "backup",
                    "enqueued",
                    "backup_schedule_due",
                    resource_id=export_job.id,
                )
            ],
        )
