from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.operations.models import WorkerLease
from backend.app.operations.stale_run_domain import stale_run_age_anchor
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.workers.jobs import JobType


class StaleRunQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def stale_runs(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int,
        statuses: set[RunStatus],
        limit: int,
        now: datetime,
    ) -> list[AgentRun]:
        cutoff = now - timedelta(seconds=stale_after_seconds)
        candidates = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status.in_([status.value for status in statuses]),
            )
            .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
            .limit(limit * 3)
        ).all()
        stale_runs = []
        for run in candidates:
            age_anchor = stale_run_age_anchor(run)
            if age_anchor is not None and age_anchor < cutoff:
                stale_runs.append(run)
        stale_runs.sort(key=lambda run: stale_run_age_anchor(run) or run.created_at)
        return stale_runs[:limit]

    def latest_worker_leases_by_run_id(
        self,
        workspace_id: UUID,
        run_ids: list[UUID],
    ) -> dict[UUID, WorkerLease]:
        if not run_ids:
            return {}
        leases = self._session.scalars(
            select(WorkerLease)
            .where(
                WorkerLease.workspace_id == workspace_id,
                WorkerLease.job_type == JobType.AGENT_RUN.value,
                WorkerLease.resource_id.in_(run_ids),
            )
            .order_by(WorkerLease.started_at.desc(), WorkerLease.created_at.desc())
        ).all()
        latest: dict[UUID, WorkerLease] = {}
        for lease in leases:
            latest.setdefault(lease.resource_id, lease)
        return latest
