from __future__ import annotations

from datetime import datetime
from uuid import UUID

from redis import Redis

from backend.app.api.schemas.operations import QueueMetricsResponse
from backend.app.operations.utils import ensure_aware_utc
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue.redis_queue import RedisQueue


class QueueMetricsService:
    def __init__(
        self,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._redis = redis
        self._keys = key_builder

    def queue_metrics(
        self,
        queue_name: str,
        workspace_id: UUID | None = None,
    ) -> QueueMetricsResponse:
        if self._redis is None:
            return QueueMetricsResponse(
                queue_name=queue_name,
                queued=0,
                dead_letter=0,
                idempotency_keys=0,
            )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        idempotency_pattern = (
            self._keys.idempotency_key(str(workspace_id), "*")
            if workspace_id is not None
            else self._keys.idempotency_key("*", "*")
        )
        return QueueMetricsResponse(
            queue_name=queue_name,
            queued=queue.count_queued(workspace_id=workspace_id),
            dead_letter=queue.count_dead_letters(workspace_id=workspace_id),
            idempotency_keys=self.count_keys(idempotency_pattern),
        )

    def oldest_queued_age_seconds(
        self,
        queue_name: str,
        now: datetime,
        scan_limit: int,
    ) -> int | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return oldest_job_age(now, queue.peek(limit=scan_limit))

    def count_keys(self, pattern: str) -> int:
        if self._redis is None:
            return 0
        return sum(1 for _ in self._redis.scan_iter(pattern))


def oldest_job_age(now: datetime, jobs: list[JobPayload]) -> int | None:
    ages = [
        max(0, int((ensure_aware_utc(now) - ensure_aware_utc(job.created_at)).total_seconds()))
        for job in jobs
    ]
    return max(ages) if ages else None
