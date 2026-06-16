from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis.exceptions import RedisError

from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters
from backend.app.operations.timeline_utils import (
    queue_job_time,
    redact_metadata,
    redact_secret_like_text,
    within,
)
from backend.app.workers.jobs import JobType
from backend.app.workers.queue.redis_queue import RedisQueue


class TeamRuntimeQueueTimelineCollector:
    def queue_events(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
        queue: RedisQueue | None,
    ) -> list[TimelineEvent]:
        if queue is None:
            return []
        try:
            jobs_by_state = {
                "queued": queue.list_queued(
                    filters.limit,
                    workspace_id=workspace_id,
                    job_type=JobType.TEAM_EXECUTION_LOOP,
                    resource_id=team_id,
                ),
                "scheduled_retry": queue.list_scheduled_retries(
                    filters.limit,
                    workspace_id=workspace_id,
                    job_type=JobType.TEAM_EXECUTION_LOOP,
                    resource_id=team_id,
                ),
                "dead_letter": queue.list_dead_letters(
                    filters.limit,
                    workspace_id=workspace_id,
                    job_type=JobType.TEAM_EXECUTION_LOOP,
                    resource_id=team_id,
                ),
            }
        except (OSError, RedisError, TimeoutError) as exc:
            return [
                TimelineEvent(
                    id=f"queue_job:unavailable:{team_id}",
                    source_type="queue_job",
                    event_type="team.runtime.queue.unavailable",
                    occurred_at=datetime.now(UTC),
                    resource_id=str(team_id),
                    message="Team execution loop queue snapshot is unavailable",
                    metadata={
                        "queue_name": queue.queue_name,
                        "job_type": JobType.TEAM_EXECUTION_LOOP.value,
                        "workspace_id": str(workspace_id),
                        "resource_id": str(team_id),
                        "error_type": type(exc).__name__,
                        "error": redact_secret_like_text(str(exc)),
                    },
                )
            ]
        events: list[TimelineEvent] = []
        for state, jobs in jobs_by_state.items():
            for job in jobs:
                occurred_at = queue_job_time(job)
                if not within(occurred_at, filters):
                    continue
                events.append(
                    TimelineEvent(
                        id=f"queue_job:{state}:{job.job_id}",
                        source_type="queue_job",
                        event_type=f"team.runtime.queue.{state}",
                        occurred_at=occurred_at,
                        resource_id=str(job.job_id),
                        message=f"Team execution loop job is {state}",
                        metadata={
                            "state": state,
                            "queue_name": queue.queue_name,
                            "job_id": str(job.job_id),
                            "job_type": job.job_type.value,
                            "workspace_id": str(job.workspace_id),
                            "resource_id": str(job.resource_id),
                            "attempt": job.attempt,
                            "max_attempts": job.max_attempts,
                            "priority": job.priority,
                            "last_error": redact_secret_like_text(job.last_error)
                            if job.last_error is not None
                            else None,
                            "last_error_type": job.last_error_type,
                            "last_failed_at": job.last_failed_at.isoformat()
                            if job.last_failed_at is not None
                            else None,
                            "routing": redact_metadata(dict(job.routing)),
                            "trace": redact_metadata(job.trace_metadata()),
                        },
                    )
                )
        return events
