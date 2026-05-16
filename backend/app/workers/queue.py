import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, cast

from redis import Redis

from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload


class JobHandler(Protocol):
    def __call__(self, job: JobPayload) -> None: ...


@dataclass(frozen=True)
class RedisQueue:
    redis: Redis
    keys: RedisKeyBuilder
    queue_name: str
    blocking_timeout_seconds: int = 1

    def enqueue(self, job: JobPayload) -> bool:
        idempotency_key = self.keys.idempotency_key(str(job.workspace_id), job.idempotency_key)
        created = self.redis.set(idempotency_key, str(job.job_id), nx=True, ex=86_400)
        if not created:
            return False

        self.redis.rpush(self.keys.queue(self.queue_name), self._serialize(job))
        return True

    def dequeue(self) -> JobPayload | None:
        result = self.redis.blpop(
            [self.keys.queue(self.queue_name)],
            timeout=self.blocking_timeout_seconds,
        )
        if result is None:
            return None
        _, raw_payload = cast(tuple[str, bytes | str], result)
        return self._deserialize(raw_payload)

    def retry_or_dead_letter(self, job: JobPayload) -> None:
        if job.can_retry:
            self.redis.rpush(self.keys.queue(self.queue_name), self._serialize(job.next_attempt()))
            return

        self.redis.rpush(
            self.keys.dead_letter_queue(self.queue_name),
            self._serialize(job.next_attempt()),
        )

    @contextmanager
    def run_lock(self, workspace_id: str, run_id: str, ttl_seconds: int = 600) -> Iterator[bool]:
        lock_key = self.keys.run_lock(workspace_id, run_id)
        acquired = bool(self.redis.set(lock_key, "1", nx=True, ex=ttl_seconds))
        try:
            yield acquired
        finally:
            if acquired:
                self.redis.delete(lock_key)

    def _serialize(self, job: JobPayload) -> str:
        return job.model_dump_json()

    def _deserialize(self, raw_payload: bytes | str) -> JobPayload:
        if isinstance(raw_payload, bytes):
            raw_payload = raw_payload.decode("utf-8")
        return JobPayload.model_validate(json.loads(raw_payload))


def consume_once(queue: RedisQueue, handler: JobHandler) -> bool:
    job = queue.dequeue()
    if job is None:
        return False

    try:
        handler(job)
    except Exception:
        queue.retry_or_dead_letter(job)
        raise

    return True
