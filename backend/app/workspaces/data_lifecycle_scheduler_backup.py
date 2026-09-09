from datetime import UTC, datetime

from pydantic import ValidationError

from backend.app.api.services.exports import WorkspaceExportService
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.data_lifecycle_policy import _backup_policy
from backend.app.workspaces.data_lifecycle_schedule import (
    _scheduled_archive_export_request,
    _scheduled_backup_due,
    _scheduled_lifecycle_detail,
)
from backend.app.workspaces.data_lifecycle_settings import _backup_settings
from backend.app.workspaces.data_lifecycle_store import LifecycleStore
from backend.app.workspaces.data_lifecycle_summary import ScheduledLifecycleSummary
from backend.app.workspaces.models import Workspace


class ScheduledBackupMixin(LifecycleStore):
    def _schedule_workspace_backup_if_due(
        self,
        workspace: Workspace,
        queue: RedisQueue,
    ) -> ScheduledLifecycleSummary:
        raw_policy = _backup_settings(workspace.settings)
        latest_job = self._repo.latest_export_job(workspace.id)
        latest_success = self._repo.latest_successful_archive_export(workspace.id)
        backup_policy = _backup_policy(
            workspace.settings,
            latest_job,
            latest_success,
            generated_at=datetime.now(UTC),
        )
        schedule_status = backup_policy["schedule_status"]
        due = _scheduled_backup_due(backup_policy)
        if not due:
            return ScheduledLifecycleSummary()

        if self._repo.has_active_archive_export_job(workspace.id):
            self._repo.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.backup_skipped",
                reason="archive_export_already_active",
                metadata={"schedule_status": schedule_status},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                backup_jobs_skipped=1,
                details=[
                    _scheduled_lifecycle_detail(
                        workspace.id,
                        "backup",
                        "skipped",
                        "archive_export_already_active",
                    )
                ],
            )

        try:
            request = _scheduled_archive_export_request(raw_policy)
        except ValidationError as exc:
            self._repo.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.backup_skipped",
                reason="invalid_archive_request",
                metadata={"error": str(exc)[:1000], "schedule_status": schedule_status},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                backup_jobs_skipped=1,
                details=[
                    _scheduled_lifecycle_detail(
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
        self._repo.record_lifecycle_schedule_event(
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
                _scheduled_lifecycle_detail(
                    workspace.id,
                    "backup",
                    "enqueued",
                    "backup_schedule_due",
                    resource_id=export_job.id,
                )
            ],
        )
