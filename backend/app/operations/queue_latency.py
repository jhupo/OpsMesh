from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis

from backend.app.api.schemas.operation_queue import QueueLatencyResponse
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.queue import RedisQueue


class OperationsQueueLatencyService:
    def __init__(
        self,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._redis = redis
        self._keys = key_builder

    def queue_latency(self, queue_name: str, workspace_id: UUID) -> QueueLatencyResponse:
        if self._redis is None:
            return empty_queue_latency(queue_name)

        queue = RedisQueue(self._redis, self._keys, queue_name)
        jobs = [job for job in queue.peek(limit=500) if job.workspace_id == workspace_id]
        if not jobs:
            return empty_queue_latency(queue_name)

        now = datetime.now(UTC)
        ages = [max(0, int((now - job.created_at).total_seconds())) for job in jobs]
        return QueueLatencyResponse(
            queue_name=queue_name,
            queued=len(jobs),
            oldest_age_seconds=max(ages),
            newest_age_seconds=min(ages),
            average_age_seconds=int(sum(ages) / len(ages)),
            highest_priority=max(job.priority for job in jobs),
        )


def empty_queue_latency(queue_name: str) -> QueueLatencyResponse:
    return QueueLatencyResponse(
        queue_name=queue_name,
        queued=0,
        oldest_age_seconds=None,
        newest_age_seconds=None,
        average_age_seconds=None,
        highest_priority=None,
    )
