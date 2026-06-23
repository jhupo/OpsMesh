from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.api.schemas.operation_queue import (
    QueueJobTypeBucketResponse,
    QueuePriorityBucketResponse,
)
from backend.app.operations.utils import age_seconds
from backend.app.workers.jobs import JobPayload


@dataclass(slots=True)
class QueuePriorityBucket:
    priority: int
    queued: int = 0
    dead_letter: int = 0
    oldest_queued_age_seconds: int | None = None

    def add_queued(self, job: JobPayload, now: datetime) -> None:
        self.queued += 1
        self.oldest_queued_age_seconds = _max_optional(
            self.oldest_queued_age_seconds,
            age_seconds(now, job.created_at) or 0,
        )

    def add_dead_letter(self) -> None:
        self.dead_letter += 1

    def response(self) -> QueuePriorityBucketResponse:
        return QueuePriorityBucketResponse(
            priority=self.priority,
            queued=self.queued,
            dead_letter=self.dead_letter,
            oldest_queued_age_seconds=self.oldest_queued_age_seconds,
        )


@dataclass(slots=True)
class QueueJobTypeBucket:
    job_type: str
    queued: int = 0
    dead_letter: int = 0
    highest_priority: int | None = None
    oldest_queued_age_seconds: int | None = None

    def add_queued(self, job: JobPayload, now: datetime) -> None:
        self.queued += 1
        self.highest_priority = _max_optional(self.highest_priority, job.priority)
        self.oldest_queued_age_seconds = _max_optional(
            self.oldest_queued_age_seconds,
            age_seconds(now, job.created_at) or 0,
        )

    def add_dead_letter(self) -> None:
        self.dead_letter += 1

    def response(self) -> QueueJobTypeBucketResponse:
        return QueueJobTypeBucketResponse(
            job_type=self.job_type,
            queued=self.queued,
            dead_letter=self.dead_letter,
            highest_priority=self.highest_priority,
            oldest_queued_age_seconds=self.oldest_queued_age_seconds,
        )


class QueueInsightBucketBuilder:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self._priority_buckets: dict[int, QueuePriorityBucket] = {}
        self._job_type_buckets: dict[str, QueueJobTypeBucket] = {}

    def add_queued_job(self, job: JobPayload) -> None:
        self._priority_bucket(job.priority).add_queued(job, self._now)
        self._job_type_bucket(str(job.job_type)).add_queued(job, self._now)

    def add_dead_letter_job(self, job: JobPayload) -> None:
        self._priority_bucket(job.priority).add_dead_letter()
        self._job_type_bucket(str(job.job_type)).add_dead_letter()

    def priority_responses(self) -> list[QueuePriorityBucketResponse]:
        return [
            bucket.response()
            for _, bucket in sorted(self._priority_buckets.items(), reverse=True)
        ]

    def job_type_responses(self) -> list[QueueJobTypeBucketResponse]:
        return [bucket.response() for _, bucket in sorted(self._job_type_buckets.items())]

    def _priority_bucket(self, priority: int) -> QueuePriorityBucket:
        return self._priority_buckets.setdefault(priority, QueuePriorityBucket(priority=priority))

    def _job_type_bucket(self, job_type: str) -> QueueJobTypeBucket:
        return self._job_type_buckets.setdefault(job_type, QueueJobTypeBucket(job_type=job_type))


def _max_optional(current: int | None, candidate: int) -> int:
    return candidate if current is None else max(current, candidate)
