from __future__ import annotations

from uuid import UUID

from backend.app.operations.queue_governance_reconcile_models import (
    QueueGovernanceReconcileCounts,
)
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.runs.models import AgentRun
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue.redis_queue import RedisQueue


class QueueGovernanceActionExecutor:
    def __init__(
        self,
        *,
        queue: RedisQueue,
        run_service: RunOrchestrationService,
    ) -> None:
        self._queue = queue
        self._run_service = run_service

    def requeue_missing_runs(
        self,
        missing_runs: list[AgentRun],
        actor_user_id: UUID,
        counts: QueueGovernanceReconcileCounts,
    ) -> None:
        for run in missing_runs[: counts.remaining]:
            if self._run_service.enqueue_run(run, actor_user_id, force=True):
                counts.requeued_missing_runs += 1
                counts.remaining -= 1
            else:
                counts.skipped_items += 1
            if counts.remaining <= 0:
                break

    def remove_queued_jobs(
        self,
        workspace_id: UUID,
        jobs: list[JobPayload],
        counts: QueueGovernanceReconcileCounts,
    ) -> int:
        removed_count = 0
        for job in jobs[: counts.remaining]:
            removed = self._queue.remove_queued_job(job.job_id, workspace_id=workspace_id)
            if removed is not None:
                removed_count += 1
                counts.remaining -= 1
            else:
                counts.skipped_items += 1
            if counts.remaining <= 0:
                break
        return removed_count
