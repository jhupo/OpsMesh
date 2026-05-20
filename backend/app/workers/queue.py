from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID, uuid4

from redis import Redis

from backend.app.redis.keys import RedisKeyBuilder
from backend.app.redis.locks import redis_lock
from backend.app.workers.jobs import JobPayload


class JobHandler(Protocol):
    def __call__(self, job: JobPayload) -> None: ...


@dataclass(frozen=True)
class RedisQueue:
    redis: Redis[str]
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
        if self.blocking_timeout_seconds <= 0:
            raw_payload = self.redis.lpop(self.keys.queue(self.queue_name))
            if raw_payload is None:
                return None
            return self._deserialize(raw_payload)

        result = self.redis.blpop(
            [self.keys.queue(self.queue_name)],
            timeout=self.blocking_timeout_seconds,
        )
        if result is None:
            return None
        _, raw_payload = cast(tuple[str, bytes | str], result)
        return self._deserialize(raw_payload)

    def dequeue_matching(
        self,
        predicate: Callable[[JobPayload], bool],
        *,
        scan_limit: int = 50,
    ) -> JobPayload | None:
        queue_key = self.keys.queue(self.queue_name)
        limit = max(1, scan_limit)
        for raw_payload in self.redis.lrange(queue_key, 0, limit - 1):
            job = self._deserialize(raw_payload)
            if not predicate(job):
                continue
            removed = self.redis.lrem(queue_key, 1, raw_payload)
            if int(removed) == 0:
                continue
            return job
        if self.blocking_timeout_seconds > 0:
            return self.dequeue()
        return None

    def retry_or_dead_letter(self, job: JobPayload) -> None:
        if job.can_retry:
            self.redis.rpush(self.keys.queue(self.queue_name), self._serialize(job.next_attempt()))
            return

        self.redis.rpush(
            self.keys.dead_letter_queue(self.queue_name),
            self._serialize(job.next_attempt()),
        )

    def list_dead_letters(
        self,
        limit: int = 50,
        *,
        workspace_id: UUID | None = None,
    ) -> list[JobPayload]:
        jobs: list[JobPayload] = []
        for raw_job in self.redis.lrange(self.keys.dead_letter_queue(self.queue_name), 0, -1):
            job = self._deserialize(raw_job)
            if workspace_id is not None and job.workspace_id != workspace_id:
                continue
            jobs.append(job)
            if len(jobs) >= limit:
                break
        return jobs

    def count_queued(self, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.llen(self.keys.queue(self.queue_name)))
        return sum(
            1
            for raw_job in self.redis.lrange(self.keys.queue(self.queue_name), 0, -1)
            if self._deserialize(raw_job).workspace_id == workspace_id
        )

    def count_dead_letters(self, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.llen(self.keys.dead_letter_queue(self.queue_name)))
        return sum(
            1
            for raw_job in self.redis.lrange(self.keys.dead_letter_queue(self.queue_name), 0, -1)
            if self._deserialize(raw_job).workspace_id == workspace_id
        )

    def peek(self, *, limit: int = 50) -> list[JobPayload]:
        if limit <= 0:
            return []
        return [
            self._deserialize(raw_job)
            for raw_job in self.redis.lrange(self.keys.queue(self.queue_name), 0, limit - 1)
        ]

    def requeue_dead_letter(
        self,
        job_id: UUID,
        *,
        workspace_id: UUID | None = None,
        reset_attempts: bool = True,
    ) -> JobPayload | None:
        dead_letter_key = self.keys.dead_letter_queue(self.queue_name)
        for raw_job in self.redis.lrange(dead_letter_key, 0, -1):
            job = self._deserialize(raw_job)
            if job.job_id != job_id:
                continue
            if workspace_id is not None and job.workspace_id != workspace_id:
                return None

            removed = self.redis.lrem(dead_letter_key, 1, raw_job)
            if int(removed) == 0:
                return None

            update = {"job_id": uuid4(), "attempt": 0} if reset_attempts else {"job_id": uuid4()}
            retry_job = job.model_copy(update=update)
            self.redis.rpush(self.keys.queue(self.queue_name), self._serialize(retry_job))
            return retry_job
        return None

    @contextmanager
    def run_lock(self, workspace_id: str, run_id: str, ttl_seconds: int = 600) -> Iterator[bool]:
        lock_key = self.keys.run_lock(workspace_id, run_id)
        with redis_lock(self.redis, lock_key, ttl_seconds) as acquired:
            yield acquired

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
