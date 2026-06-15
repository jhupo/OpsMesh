from __future__ import annotations

from uuid import UUID

from redis import Redis

from backend.app.api.schemas.operations import DeadLetterJobsResponse
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue


class DeadLetterQueueService:
    def __init__(
        self,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

    def list_dead_letters(
        self,
        workspace_id: UUID,
        queue_name: str,
        limit: int,
    ) -> DeadLetterJobsResponse:
        if self._redis is None:
            return DeadLetterJobsResponse(items=[], total=0)
        queue = RedisQueue(self._redis, self._keys, queue_name)
        items = queue.list_dead_letters(limit, workspace_id=workspace_id)
        total = queue.count_dead_letters(workspace_id=workspace_id)
        return DeadLetterJobsResponse(items=items, total=total)

    def requeue_dead_letter(
        self,
        workspace_id: UUID,
        queue_name: str,
        job_id: UUID,
    ) -> JobPayload | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.requeue_dead_letter(job_id, workspace_id=workspace_id)
