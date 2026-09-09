from __future__ import annotations

from datetime import datetime
from uuid import UUID

from backend.app.scheduled_jobs.constants import RECORD_DUE_ACTION
from backend.app.scheduled_jobs.contracts import ScheduledJobStore
from backend.app.scheduled_jobs.models import WorkspaceScheduledJob
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue


class ScheduledJobDispatcherMixin(ScheduledJobStore):
    def _apply_due_action(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        due_at: datetime,
        queue: RedisQueue | None,
    ) -> tuple[str, UUID | None, str | None]:
        if scheduled_job.action_type == RECORD_DUE_ACTION:
            return "recorded", None, "Due action recorded"
        if queue is None:
            return "skipped", None, "Worker queue is unavailable"
        if scheduled_job.job_type is None or scheduled_job.resource_id is None:
            return "skipped", None, "Queue job action is incomplete"
        try:
            job_type = JobType(scheduled_job.job_type)
        except ValueError:
            return "skipped", None, "Queue job type is unsupported"
        queued_job = JobPayload(
            workspace_id=scheduled_job.workspace_id,
            job_type=job_type,
            resource_id=scheduled_job.resource_id,
            requested_by_user_id=scheduled_job.created_by_user_id,
            routing=scheduled_job.routing,
            priority=scheduled_job.priority,
            max_attempts=scheduled_job.max_attempts,
            idempotency_key=(
                "workspace.scheduled_job:"
                f"{scheduled_job.workspace_id}:{scheduled_job.id}:{due_at.isoformat()}"
            ),
        )
        if not queue.enqueue(queued_job):
            return "skipped", None, "Queue idempotency key already exists"
        return "enqueued", queued_job.job_id, None


def increment_count(counts: dict[str, int], key: str | None) -> None:
    item = key or "unspecified"
    counts[item] = counts.get(item, 0) + 1
