from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.orchestration.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task


@dataclass(frozen=True, slots=True)
class ActiveRunPage:
    total_active_runs: int
    status_counts: dict[str, int]
    runs: list[AgentRun]


class RunActivityQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def active_run_page(
        self,
        workspace_id: UUID,
        *,
        team_id: UUID | None,
        scan_limit: int,
    ) -> ActiveRunPage:
        statement = active_runs_statement(workspace_id, team_id=team_id)
        active_runs_subquery = statement.order_by(None).subquery()
        total_active_runs = int(
            self._session.scalar(select(func.count()).select_from(active_runs_subquery)) or 0
        )
        status_counts = {
            str(status): int(count)
            for status, count in self._session.execute(
                select(active_runs_subquery.c.status, func.count()).group_by(
                    active_runs_subquery.c.status
                )
            )
        }
        runs = self._session.scalars(
            statement.order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc()).limit(
                scan_limit
            )
        ).all()
        return ActiveRunPage(
            total_active_runs=total_active_runs,
            status_counts=dict(sorted(status_counts.items())),
            runs=list(runs),
        )

    def latest_run_events_by_run_id(
        self,
        workspace_id: UUID,
        run_ids: list[UUID],
    ) -> dict[UUID, RunEvent]:
        if not run_ids:
            return {}
        latest_sequences = (
            select(
                RunEvent.agent_run_id.label("agent_run_id"),
                func.max(RunEvent.sequence).label("latest_sequence"),
            )
            .where(
                RunEvent.workspace_id == workspace_id,
                RunEvent.agent_run_id.in_(run_ids),
            )
            .group_by(RunEvent.agent_run_id)
            .subquery()
        )
        events = self._session.scalars(
            select(RunEvent).join(
                latest_sequences,
                (RunEvent.agent_run_id == latest_sequences.c.agent_run_id)
                & (RunEvent.sequence == latest_sequences.c.latest_sequence),
            )
        ).all()
        return {event.agent_run_id: event for event in events}


def active_runs_statement(
    workspace_id: UUID,
    *,
    team_id: UUID | None,
) -> Select[tuple[AgentRun]]:
    statement = select(AgentRun).where(
        AgentRun.workspace_id == workspace_id,
            AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
    )
    if team_id is None:
        return statement
    return statement.join(Task, Task.id == AgentRun.task_id).where(
        Task.workspace_id == workspace_id,
        Task.agent_team_id == team_id,
    )
