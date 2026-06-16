from __future__ import annotations

from uuid import UUID

from backend.app.admin.base import AdminRedisService
from backend.app.api.schemas.operations import QueueMetricsResponse
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue


class AdminQueueOperationsService(AdminRedisService):
    def queue_metrics(self, queue_name: str) -> QueueMetricsResponse:
        if self._redis is None:
            return QueueMetricsResponse(
                queue_name=queue_name,
                queued=0,
                dead_letter=0,
                idempotency_keys=0,
            )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return QueueMetricsResponse(
            queue_name=queue_name,
            queued=queue.count_queued(),
            dead_letter=queue.count_dead_letters(),
            idempotency_keys=self._count_keys(self._keys.idempotency_key("*", "*")),
        )

    def list_dead_letters(self, queue_name: str, limit: int) -> tuple[list[JobPayload], int]:
        if self._redis is None:
            return [], 0
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.list_dead_letters(limit), queue.count_dead_letters()

    def requeue_dead_letter(self, queue_name: str, job_id: UUID) -> JobPayload | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.requeue_dead_letter(job_id, workspace_id=None)

    def oldest_queued_at(self, queue_name: str) -> str | None:
        queued_jobs = self._queued_jobs(queue_name)
        if not queued_jobs:
            return None
        return min(job.created_at for job in queued_jobs).isoformat()

    def highest_queue_priority(self, queue_name: str) -> int | None:
        queued_jobs = self._queued_jobs(queue_name)
        if not queued_jobs:
            return None
        return max(job.priority for job in queued_jobs)

    def _queued_jobs(self, queue_name: str, limit: int = 500) -> list[JobPayload]:
        if self._redis is None:
            return []
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.peek(limit=limit)
