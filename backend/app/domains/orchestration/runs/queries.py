"""Shared run queries used by scheduling and execution-domain projections."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.orchestration.workflows.statuses import ACTIVE_RUN_STATUS_VALUES


def active_task_ids_by_agent(
    session: Session,
    *,
    workspace_id: UUID,
    agent_profile_ids: set[UUID],
) -> dict[UUID, set[UUID]]:
    """Return active task ids grouped by assigned agent within one workspace."""
    if not agent_profile_ids:
        return {}
    result: dict[UUID, set[UUID]] = defaultdict(set)
    rows = session.execute(
        select(AgentRun.agent_profile_id, AgentRun.task_id).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.agent_profile_id.in_(agent_profile_ids),
            AgentRun.task_id.is_not(None),
            AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
        )
    ).all()
    for agent_profile_id, task_id in rows:
        if agent_profile_id is None or task_id is None:
            continue
        result[agent_profile_id].add(task_id)
    return dict(result)


def task_for_run(session: Session, run: AgentRun) -> Task | None:
    """Load a run's task while preserving the run workspace boundary."""
    if run.task_id is None:
        return None
    task = session.get(Task, run.task_id)
    if task is None or task.workspace_id != run.workspace_id:
        return None
    return task


def authorization_snapshot_for_run(run: AgentRun) -> dict[str, object]:
    """Read the immutable authorization snapshot embedded in a run input."""
    run_input = run.input if isinstance(run.input, dict) else {}
    snapshot = run_input.get("authorization_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def latest_events_by_run(
    session: Session,
    workspace_id: UUID,
    runs: list[AgentRun],
) -> dict[UUID, RunEvent]:
    """Return the newest event for each run in a workspace."""
    run_ids = [run.id for run in runs]
    if not run_ids:
        return {}
    events = session.scalars(
        select(RunEvent)
        .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id.in_(run_ids))
        .order_by(RunEvent.agent_run_id.asc(), RunEvent.sequence.desc())
    ).all()
    latest: dict[UUID, RunEvent] = {}
    for event in events:
        latest.setdefault(event.agent_run_id, event)
    return latest


def run_events_for_runs(
    session: Session,
    workspace_id: UUID,
    runs: list[AgentRun],
) -> list[RunEvent]:
    """Return all events for the supplied runs in execution order."""
    run_ids = [run.id for run in runs]
    if not run_ids:
        return []
    return list(
        session.scalars(
            select(RunEvent)
            .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id.in_(run_ids))
            .order_by(RunEvent.created_at.asc(), RunEvent.sequence.asc())
        )
    )


class RunQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_runs(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentRun], int]:
        statement = select(AgentRun).where(AgentRun.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        return page_scalars(
            self._session,
            statement.order_by(AgentRun.created_at.desc()),
            page,
        )

    def list_events(
        self,
        workspace_id: UUID,
        agent_run_id: UUID,
        page: PageParams,
    ) -> tuple[list[RunEvent], int]:
        statement = (
            select(RunEvent)
            .where(
                RunEvent.workspace_id == workspace_id,
                RunEvent.agent_run_id == agent_run_id,
            )
            .order_by(RunEvent.sequence.asc())
        )
        return page_scalars(self._session, statement, page)
