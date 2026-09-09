from __future__ import annotations

from datetime import datetime

from backend.app.scheduled_jobs.contracts import ScheduledJobStore
from backend.app.scheduled_jobs.dispatcher import increment_count
from backend.app.scheduled_jobs.models import WorkspaceScheduledJobEvent
from backend.app.scheduled_jobs.schedule import utc_datetime
from backend.app.scheduled_jobs.types import ScheduledJobMaintenanceSummary
from backend.app.workers.queue.redis_queue import RedisQueue


class ScheduledJobMaintenanceMixin(ScheduledJobStore):
    def enqueue_due(
        self,
        *,
        queue: RedisQueue | None,
        limit: int = 100,
        now: datetime | None = None,
    ) -> ScheduledJobMaintenanceSummary:
        current_time = utc_datetime(now)
        due_jobs = self._due_jobs(limit=limit, now=current_time)

        enqueued = 0
        recorded = 0
        skipped = 0
        enqueued_by_job_type: dict[str, int] = {}
        recorded_by_job_type: dict[str, int] = {}
        skipped_by_job_type: dict[str, int] = {}
        for scheduled_job in due_jobs:
            due_at = utc_datetime(scheduled_job.next_run_at or current_time)
            event_status, queued_job_id, message = self._apply_due_action(
                scheduled_job,
                due_at=due_at,
                queue=queue,
            )
            job_type = scheduled_job.job_type or scheduled_job.action_type
            if event_status == "enqueued":
                enqueued += 1
                increment_count(enqueued_by_job_type, job_type)
            elif event_status == "recorded":
                recorded += 1
                increment_count(recorded_by_job_type, job_type)
            else:
                skipped += 1
                increment_count(skipped_by_job_type, job_type)
            self._advance_schedule(scheduled_job, due_at=due_at, now=current_time)
            self._session.add(
                WorkspaceScheduledJobEvent(
                    workspace_id=scheduled_job.workspace_id,
                    scheduled_job_id=scheduled_job.id,
                    due_at=due_at,
                    action_type=scheduled_job.action_type,
                    status=event_status,
                    queued_job_id=queued_job_id,
                    message=message,
                    metadata_={
                        "scheduled_job_name": scheduled_job.name,
                        "job_type": scheduled_job.job_type,
                        "resource_id": str(scheduled_job.resource_id)
                        if scheduled_job.resource_id is not None
                        else None,
                        "metadata": scheduled_job.metadata_,
                    },
                )
            )
            self._record_due_audit(
                scheduled_job,
                status=event_status,
                queued_job_id=queued_job_id,
                message=message,
            )

        if due_jobs:
            self._session.commit()
        return ScheduledJobMaintenanceSummary(
            enqueued=enqueued,
            recorded=recorded,
            skipped=skipped,
            enqueued_by_job_type=dict(sorted(enqueued_by_job_type.items())),
            recorded_by_job_type=dict(sorted(recorded_by_job_type.items())),
            skipped_by_job_type=dict(sorted(skipped_by_job_type.items())),
        )
