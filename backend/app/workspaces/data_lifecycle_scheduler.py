from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.files.storage import ObjectStorage
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.data_lifecycle_scheduler_backup import ScheduledBackupMixin
from backend.app.workspaces.data_lifecycle_scheduler_queries import ScheduledLifecycleQueryMixin
from backend.app.workspaces.data_lifecycle_scheduler_restore import ScheduledRestoreDrillMixin
from backend.app.workspaces.data_lifecycle_scheduler_retention import ScheduledRetentionMixin
from backend.app.workspaces.data_lifecycle_summary import ScheduledLifecycleSummary
from backend.app.workspaces.models import Workspace


class WorkspaceScheduledLifecycleService(
    ScheduledLifecycleQueryMixin,
    ScheduledBackupMixin,
    ScheduledRetentionMixin,
    ScheduledRestoreDrillMixin,
):
    def __init__(self, session: Session) -> None:
        self._session = session

    def run_scheduled_lifecycle(
        self,
        *,
        queue: RedisQueue,
        storage: ObjectStorage | None = None,
        workspace_id: UUID | None = None,
        limit: int = 100,
    ) -> ScheduledLifecycleSummary:
        statement = select(Workspace).where(Workspace.status == "active")
        if workspace_id is not None:
            statement = statement.where(Workspace.id == workspace_id)
        workspaces = self._session.scalars(
            statement.order_by(Workspace.created_at.asc(), Workspace.id.asc()).limit(limit)
        ).all()

        summary = ScheduledLifecycleSummary(scanned_workspaces=len(workspaces))
        for workspace in workspaces:
            summary = summary.combine(
                self._run_workspace_scheduled_lifecycle(
                    workspace,
                    queue,
                    storage=storage,
                )
            )
        return summary

    def _run_workspace_scheduled_lifecycle(
        self,
        workspace: Workspace,
        queue: RedisQueue,
        *,
        storage: ObjectStorage | None,
    ) -> ScheduledLifecycleSummary:
        backup_summary = self._schedule_workspace_backup_if_due(workspace, queue)
        retention_summary = self._run_workspace_retention_if_due(workspace)
        restore_drill_summary = self._run_workspace_restore_drill_if_due(
            workspace,
            storage=storage,
        )
        return backup_summary.combine(retention_summary).combine(restore_drill_summary)
