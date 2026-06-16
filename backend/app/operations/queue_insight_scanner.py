from __future__ import annotations

from uuid import UUID

from redis import Redis

from backend.app.operations.queue_insight_models import QueueInsightScan
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.queue.redis_queue import RedisQueue


class QueueInsightScanner:
    def __init__(
        self,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._redis = redis
        self._keys = key_builder

    def scan(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int,
    ) -> QueueInsightScan:
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return QueueInsightScan(
            queued_total=queue.count_queued(workspace_id=workspace_id),
            dead_letter_total=queue.count_dead_letters(workspace_id=workspace_id),
            queued_jobs=[
                job for job in queue.peek(limit=scan_limit) if job.workspace_id == workspace_id
            ],
            dead_letter_jobs=queue.list_dead_letters(scan_limit, workspace_id=workspace_id),
        )
