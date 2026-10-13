from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.resources.lifecycle.rollup import WorkspaceDataLifecycleRollupService
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.temporal.models import TemporalOutboxEntry


class WorkspaceOperationsOverviewResponse(BaseModel):
    runs_by_status: dict[str, int]
    runtimes_by_status: dict[str, int]
    pending_temporal_dispatches: int
    security_warnings: int
    data_lifecycle: dict[str, object]


class WorkspaceOperationsOverviewService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def summarize(self, workspace_id: UUID) -> WorkspaceOperationsOverviewResponse:
        return WorkspaceOperationsOverviewResponse(
            runs_by_status=dict(
                self.session.execute(
                    select(AgentRun.status, func.count())
                    .where(AgentRun.workspace_id == workspace_id)
                    .group_by(AgentRun.status)
                )
                .tuples()
                .all()
            ),
            runtimes_by_status=dict(
                self.session.execute(
                    select(WorkspaceRuntime.status, func.count())
                    .where(WorkspaceRuntime.workspace_id == workspace_id)
                    .group_by(WorkspaceRuntime.status)
                )
                .tuples()
                .all()
            ),
            pending_temporal_dispatches=int(
                self.session.scalar(
                    select(func.count())
                    .select_from(TemporalOutboxEntry)
                    .where(
                        TemporalOutboxEntry.workspace_id == workspace_id,
                        TemporalOutboxEntry.published_at.is_(None),
                    )
                )
                or 0
            ),
            security_warnings=int(
                self.session.scalar(
                    select(func.count())
                    .select_from(SecurityEvent)
                    .where(
                        SecurityEvent.workspace_id == workspace_id,
                        SecurityEvent.severity.in_(("warning", "critical")),
                    )
                )
                or 0
            ),
            data_lifecycle=WorkspaceDataLifecycleRollupService(self.session).data_lifecycle_rollup(
                workspace_id
            ),
        )
