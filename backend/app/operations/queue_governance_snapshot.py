from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.operations.queue_governance_models import QueueGovernanceSnapshot
from backend.app.operations.queue_governance_time import aware_datetime
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue


class QueueGovernanceSnapshotBuilder:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

    def build(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int,
        stale_after_seconds: int,
    ) -> QueueGovernanceSnapshot:
        scan_limit = max(1, min(scan_limit, 5_000))
        stale_after_seconds = max(60, min(stale_after_seconds, 86_400))
        now = datetime.now(UTC)
        if self._redis is None:
            return _empty_snapshot(now, queue_name, scan_limit, stale_after_seconds)

        queue = RedisQueue(self._redis, self._keys, queue_name)
        workspace_jobs = _workspace_jobs(queue.peek(limit=scan_limit), workspace_id)
        agent_run_jobs = [job for job in workspace_jobs if job.job_type == JobType.AGENT_RUN]
        runs_by_id = self._runs_by_id(workspace_id, {job.resource_id for job in agent_run_jobs})

        queued_total = queue.count_queued(workspace_id=workspace_id)
        return QueueGovernanceSnapshot(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
            queued_total=queued_total,
            queued_scanned=len(workspace_jobs),
            agent_run_jobs=agent_run_jobs,
            dead_letter_total=queue.count_dead_letters(workspace_id=workspace_id),
            orphaned_jobs=_orphaned_jobs(agent_run_jobs, runs_by_id),
            non_runnable_jobs=_non_runnable_jobs(agent_run_jobs, runs_by_id),
            duplicate_jobs=_duplicate_jobs(agent_run_jobs),
            missing_runs=self._missing_runs(
                workspace_id=workspace_id,
                truncated=_scan_truncated(queue, scan_limit, queued_total, len(workspace_jobs)),
                queued_run_ids=_queued_run_ids(agent_run_jobs, runs_by_id),
            ),
            old_queued_jobs=_old_queued_jobs(now, workspace_jobs, stale_after_seconds),
            truncated=_scan_truncated(queue, scan_limit, queued_total, len(workspace_jobs)),
        )

    def _runs_by_id(self, workspace_id: UUID, run_ids: set[UUID]) -> dict[UUID, AgentRun]:
        if not run_ids:
            return {}
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.id.in_(run_ids),
            )
        ).all()
        return {run.id: run for run in runs}

    def _missing_runs(
        self,
        *,
        workspace_id: UUID,
        truncated: bool,
        queued_run_ids: set[UUID],
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


def _empty_snapshot(
    generated_at: datetime,
    queue_name: str,
    scan_limit: int,
    stale_after_seconds: int,
) -> QueueGovernanceSnapshot:
    return QueueGovernanceSnapshot(
        generated_at=generated_at,
        queue_name=queue_name,
        scan_limit=scan_limit,
        stale_after_seconds=stale_after_seconds,
        queued_total=0,
        queued_scanned=0,
        agent_run_jobs=[],
        dead_letter_total=0,
        orphaned_jobs=[],
        non_runnable_jobs=[],
        duplicate_jobs=[],
        missing_runs=[],
        old_queued_jobs=[],
        truncated=False,
    )


def _workspace_jobs(jobs: list[JobPayload], workspace_id: UUID) -> list[JobPayload]:
    return [job for job in jobs if job.workspace_id == workspace_id]


def _scan_truncated(
    queue: RedisQueue,
    scan_limit: int,
    queued_total: int,
    workspace_jobs_scanned: int,
) -> bool:
    return queue.count_queued() > scan_limit or queued_total > workspace_jobs_scanned


def _orphaned_jobs(
    agent_run_jobs: list[JobPayload],
    runs_by_id: dict[UUID, AgentRun],
) -> list[JobPayload]:
    return [job for job in agent_run_jobs if job.resource_id not in runs_by_id]


def _non_runnable_jobs(
    agent_run_jobs: list[JobPayload],
    runs_by_id: dict[UUID, AgentRun],
) -> list[JobPayload]:
    return [
        job
        for job in agent_run_jobs
        if job.resource_id in runs_by_id
        and runs_by_id[job.resource_id].status != RunStatus.QUEUED.value
    ]


def _duplicate_jobs(agent_run_jobs: list[JobPayload]) -> list[JobPayload]:
    jobs_by_run: dict[UUID, list[JobPayload]] = {}
    for job in agent_run_jobs:
        jobs_by_run.setdefault(job.resource_id, []).append(job)
    return [duplicate for jobs in jobs_by_run.values() if len(jobs) > 1 for duplicate in jobs[1:]]


def _queued_run_ids(
    agent_run_jobs: list[JobPayload],
    runs_by_id: dict[UUID, AgentRun],
) -> set[UUID]:
    return {
        job.resource_id
        for job in agent_run_jobs
        if job.resource_id in runs_by_id
        and runs_by_id[job.resource_id].status == RunStatus.QUEUED.value
    }


def _old_queued_jobs(
    generated_at: datetime,
    workspace_jobs: list[JobPayload],
    stale_after_seconds: int,
) -> list[JobPayload]:
    cutoff = generated_at - timedelta(seconds=stale_after_seconds)
    return [job for job in workspace_jobs if aware_datetime(job.created_at) < cutoff]
