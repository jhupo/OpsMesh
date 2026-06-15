from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from redis import Redis

from backend.app.api.schemas.operations import (
    OperationsQueueInsightsResponse,
    QueueJobTypeBucketResponse,
    QueuePriorityBucketResponse,
)
from backend.app.operations.queue_governance_time import aware_datetime, max_optional_int
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue


class QueueInsightsService:
    def __init__(
        self,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

    def queue_insights(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
    ) -> OperationsQueueInsightsResponse:
        now = datetime.now(UTC)
        scan_limit = max(1, min(scan_limit, 5_000))
        if self._redis is None:
            return _empty_insights(now, queue_name, scan_limit)

        queue = RedisQueue(self._redis, self._keys, queue_name)
        queued_total = queue.count_queued(workspace_id=workspace_id)
        dead_letter_total = queue.count_dead_letters(workspace_id=workspace_id)
        queued_jobs = [
            job for job in queue.peek(limit=scan_limit) if job.workspace_id == workspace_id
        ]
        dead_letter_jobs = queue.list_dead_letters(scan_limit, workspace_id=workspace_id)
        priority_buckets = _priority_buckets(now, queued_jobs, dead_letter_jobs)
        job_type_buckets = _job_type_buckets(now, queued_jobs, dead_letter_jobs)

        return OperationsQueueInsightsResponse(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            queued_total=queued_total,
            dead_letter_total=dead_letter_total,
            queued_scanned=len(queued_jobs),
            dead_letter_scanned=len(dead_letter_jobs),
            truncated=queued_total > len(queued_jobs) or dead_letter_total > len(dead_letter_jobs),
            oldest_queued_age_seconds=max(
                [
                    max(0, int((now - aware_datetime(job.created_at)).total_seconds()))
                    for job in queued_jobs
                ],
                default=None,
            ),
            highest_priority=max([job.priority for job in queued_jobs], default=None),
            priority_buckets=[
                QueuePriorityBucketResponse(
                    priority=priority,
                    queued=int(counts["queued"] or 0),
                    dead_letter=int(counts["dead_letter"] or 0),
                    oldest_queued_age_seconds=cast(
                        int | None,
                        counts["oldest_queued_age_seconds"],
                    ),
                )
                for priority, counts in sorted(priority_buckets.items(), reverse=True)
            ],
            job_type_buckets=[
                QueueJobTypeBucketResponse(
                    job_type=job_type,
                    queued=int(counts["queued"] or 0),
                    dead_letter=int(counts["dead_letter"] or 0),
                    highest_priority=cast(int | None, counts["highest_priority"]),
                    oldest_queued_age_seconds=cast(
                        int | None,
                        counts["oldest_queued_age_seconds"],
                    ),
                )
                for job_type, counts in sorted(job_type_buckets.items())
            ],
        )


def _empty_insights(
    generated_at: datetime,
    queue_name: str,
    scan_limit: int,
) -> OperationsQueueInsightsResponse:
    return OperationsQueueInsightsResponse(
        generated_at=generated_at,
        queue_name=queue_name,
        scan_limit=scan_limit,
        queued_total=0,
        dead_letter_total=0,
        queued_scanned=0,
        dead_letter_scanned=0,
        truncated=False,
        oldest_queued_age_seconds=None,
        highest_priority=None,
        priority_buckets=[],
        job_type_buckets=[],
    )


def _priority_buckets(
    now: datetime,
    queued_jobs: list[JobPayload],
    dead_letter_jobs: list[JobPayload],
) -> dict[int, dict[str, int | None]]:
    buckets: dict[int, dict[str, int | None]] = {}
    for job in queued_jobs:
        age_seconds = max(0, int((now - aware_datetime(job.created_at)).total_seconds()))
        bucket = buckets.setdefault(
            job.priority,
            {"queued": 0, "dead_letter": 0, "oldest_queued_age_seconds": None},
        )
        bucket["queued"] = int(bucket["queued"] or 0) + 1
        bucket["oldest_queued_age_seconds"] = max_optional_int(
            bucket["oldest_queued_age_seconds"],
            age_seconds,
        )
    for job in dead_letter_jobs:
        bucket = buckets.setdefault(
            job.priority,
            {"queued": 0, "dead_letter": 0, "oldest_queued_age_seconds": None},
        )
        bucket["dead_letter"] = int(bucket["dead_letter"] or 0) + 1
    return buckets


def _job_type_buckets(
    now: datetime,
    queued_jobs: list[JobPayload],
    dead_letter_jobs: list[JobPayload],
) -> dict[str, dict[str, int | None]]:
    buckets: dict[str, dict[str, int | None]] = {}
    for job in queued_jobs:
        age_seconds = max(0, int((now - aware_datetime(job.created_at)).total_seconds()))
        bucket = buckets.setdefault(
            str(job.job_type),
            {
                "queued": 0,
                "dead_letter": 0,
                "highest_priority": None,
                "oldest_queued_age_seconds": None,
            },
        )
        bucket["queued"] = int(bucket["queued"] or 0) + 1
        bucket["highest_priority"] = max_optional_int(bucket["highest_priority"], job.priority)
        bucket["oldest_queued_age_seconds"] = max_optional_int(
            bucket["oldest_queued_age_seconds"],
            age_seconds,
        )
    for job in dead_letter_jobs:
        bucket = buckets.setdefault(
            str(job.job_type),
            {
                "queued": 0,
                "dead_letter": 0,
                "highest_priority": None,
                "oldest_queued_age_seconds": None,
            },
        )
        bucket["dead_letter"] = int(bucket["dead_letter"] or 0) + 1
    return buckets
