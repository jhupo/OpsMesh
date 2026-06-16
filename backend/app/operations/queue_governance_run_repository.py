from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus


class QueueGovernanceRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def runs_by_id(self, workspace_id: UUID, run_ids: set[UUID]) -> dict[UUID, AgentRun]:
        if not run_ids:
            return {}
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.id.in_(run_ids),
            )
        ).all()
        return {run.id: run for run in runs}

    def queued_runs_missing_jobs(
        self,
        *,
        workspace_id: UUID,
        queued_run_ids: set[UUID],
        truncated: bool,
    ) -> list[AgentRun]:
        if truncated:
            return []
        runs = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status == RunStatus.QUEUED.value,
            )
            .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
        ).all()
        return [run for run in runs if run.id not in queued_run_ids]
