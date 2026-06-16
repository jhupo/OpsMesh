from __future__ import annotations

from uuid import UUID

from redis import Redis

from backend.app.core.typing import string_list
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue


class WorkerLifecycleQueueReader:
    def __init__(
        self,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._redis = redis
        self._keys = key_builder

    def queued_jobs(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        limit: int = 1_000,
    ) -> list[JobPayload]:
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return [job for job in queue.peek(limit=limit) if job.workspace_id == workspace_id]


def job_worker_types(job: JobPayload) -> list[str]:
    worker_types = string_list(job.routing.get("worker_types"))
    return worker_types or ["unrouted"]
