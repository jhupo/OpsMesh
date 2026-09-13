from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.storage.storage import ObjectStorage
from backend.app.domains.workspace.tenants.lifecycle.scheduling.backup import (
    ScheduledBackupService,
)
from backend.app.domains.workspace.tenants.lifecycle.scheduling.restore import (
    ScheduledRestoreDrillService,
)
from backend.app.domains.workspace.tenants.lifecycle.scheduling.retention import (
    ScheduledRetentionService,
)
from backend.app.domains.workspace.tenants.lifecycle.scheduling.summary import (
    ScheduledLifecycleSummary,
)
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.runtime.workers.queue import RedisQueue


class WorkspaceScheduledLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._backup = ScheduledBackupService(session)
        self._retention = ScheduledRetentionService(session)
        self._restore_drill = ScheduledRestoreDrillService(session)

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
        backup_summary = self._backup.run_if_due(workspace, queue)
        retention_summary = self._retention.run_if_due(workspace)
        restore_drill_summary = self._restore_drill.run_if_due(
            workspace,
            storage=storage,
        )
        return backup_summary.combine(retention_summary).combine(restore_drill_summary)
