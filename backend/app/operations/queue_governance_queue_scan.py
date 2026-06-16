from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from backend.app.operations.queue_governance_time import aware_datetime
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue


@dataclass(frozen=True, slots=True)
class QueueGovernanceQueueScan:
    queued_total: int
    dead_letter_total: int
    workspace_jobs: list[JobPayload]
    agent_run_jobs: list[JobPayload]
    old_queued_jobs: list[JobPayload]
    truncated: bool


class QueueGovernanceQueueScanner:
    def __init__(self, queue: RedisQueue) -> None:
        self._queue = queue

    def scan(
        self,
        *,
        workspace_id: UUID,
        scan_limit: int,
        generated_at: datetime,
        stale_after_seconds: int,
    ) -> QueueGovernanceQueueScan:
        workspace_jobs = [
            job for job in self._queue.peek(limit=scan_limit) if job.workspace_id == workspace_id
        ]
        queued_total = self._queue.count_queued(workspace_id=workspace_id)
        return QueueGovernanceQueueScan(
            queued_total=queued_total,
            dead_letter_total=self._queue.count_dead_letters(workspace_id=workspace_id),
            workspace_jobs=workspace_jobs,
            agent_run_jobs=[job for job in workspace_jobs if job.job_type == JobType.AGENT_RUN],
            old_queued_jobs=_old_queued_jobs(
                generated_at,
                workspace_jobs,
                stale_after_seconds,
            ),
            truncated=self._queue.count_queued() > scan_limit
            or queued_total > len(workspace_jobs),
        )


def _old_queued_jobs(
    generated_at: datetime,
    workspace_jobs: list[JobPayload],
    stale_after_seconds: int,
) -> list[JobPayload]:
    cutoff = generated_at - timedelta(seconds=stale_after_seconds)
    return [job for job in workspace_jobs if aware_datetime(job.created_at) < cutoff]
