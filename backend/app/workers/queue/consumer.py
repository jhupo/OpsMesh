from __future__ import annotations

from typing import Protocol

from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue.redis_queue import RedisQueue


class JobHandler(Protocol):
    def __call__(self, job: JobPayload) -> None: ...


def consume_once(queue: RedisQueue, handler: JobHandler) -> bool:
    job = queue.dequeue()
    if job is None:
        return False

    try:
        handler(job)
    except Exception:
        queue.retry_or_dead_letter(job)
        raise

    queue.ack(job)
    return True
