from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.orchestration.runs.events import RunEventRecorder
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.service import RunOrchestrationService
from backend.app.domains.orchestration.runs.state import RunStatus
from backend.app.runtime.workers.contracts import JobType
from backend.app.runtime.workers.models import WorkerLease
from backend.app.runtime.workers.queue import RedisQueue


@dataclass(frozen=True, slots=True)
class QueueRehydrationSummary:
    scanned_runs: int = 0
    requeued_runs: int = 0
    skipped_present: int = 0
    skipped_active: int = 0
    failed_runs: int = 0


class QueueRehydrationService:
    """Rebuild the Redis projection from durable queued agent-run state."""

    def __init__(self, session: Session, queue: RedisQueue) -> None:
        self._session = session
        self._queue = queue

    def rehydrate_queued_runs(self, *, limit: int = 100) -> QueueRehydrationSummary:
        if limit <= 0:
            return QueueRehydrationSummary()

        runs = list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.status == RunStatus.QUEUED.value)
                .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
                .limit(limit)
            ).all()
        )
        if not runs:
            return QueueRehydrationSummary()

        run_ids = {run.id for run in runs}
        active_ids = {
            resource_id
            for resource_id in self._session.scalars(
                select(WorkerLease.resource_id).where(
                    WorkerLease.job_type == JobType.AGENT_RUN.value,
                    WorkerLease.resource_id.in_(run_ids),
                    WorkerLease.status == "running",
                )
            ).all()
        }
        present_ids = self._queue.queued_job_resource_ids(job_type=JobType.AGENT_RUN)
        present_ids.update(
            self._queue.processing_job_resource_ids(job_type=JobType.AGENT_RUN)
        )
        present_ids.update(
            self._queue.scheduled_retry_job_resource_ids(job_type=JobType.AGENT_RUN)
        )

        summary = QueueRehydrationSummary(scanned_runs=len(runs))
        orchestration = RunOrchestrationService(self._session, queue=self._queue)
        for run in runs:
            if run.id in active_ids:
                summary = _replace_summary(summary, skipped_active=summary.skipped_active + 1)
                continue
            if run.id in present_ids:
                summary = _replace_summary(summary, skipped_present=summary.skipped_present + 1)
                continue
            try:
                if not orchestration.enqueue_run(run, requested_by_user_id=None, force=True):
                    summary = _replace_summary(summary, failed_runs=summary.failed_runs + 1)
                    continue
                RunEventRecorder(self._session).append_event(
                    run,
                    "run.queue_rehydrated",
                    "Queued run restored to the worker queue from durable state",
                    {"source": "worker_maintenance", "queue_name": self._queue.queue_name},
                )
                summary = _replace_summary(summary, requeued_runs=summary.requeued_runs + 1)
            except Exception:
                self._session.rollback()
                summary = _replace_summary(summary, failed_runs=summary.failed_runs + 1)
        return summary


def _replace_summary(summary: QueueRehydrationSummary, **updates: int) -> QueueRehydrationSummary:
    values = {
        "scanned_runs": summary.scanned_runs,
        "requeued_runs": summary.requeued_runs,
        "skipped_present": summary.skipped_present,
        "skipped_active": summary.skipped_active,
        "failed_runs": summary.failed_runs,
    }
    values.update(updates)
    return QueueRehydrationSummary(**values)
