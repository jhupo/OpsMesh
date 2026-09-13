"""Redis-backed worker queue and its lease/retry lifecycle.

The queue is intentionally one cohesive module.  Enqueueing, leasing, retrying and inspection
share the same serialization and Redis-key invariants, so splitting each operation into mixins
made the ownership and failure semantics harder to follow without providing a real extension
boundary.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, TypedDict
from uuid import UUID, uuid4

from opentelemetry.trace import SpanKind
from redis import Redis

from backend.app.core.common.trace_context import current_trace_context, telemetry_span
from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.runtime.workers.contracts import JobPayload, JobType

ENQUEUE_SCRIPT = """
if ARGV[3] == "1" then
    redis.call("SET", KEYS[1], ARGV[1], "EX", ARGV[4])
    redis.call("RPUSH", KEYS[2], ARGV[2])
    return 1
end
local reserved = redis.call("SET", KEYS[1], ARGV[1], "NX", "EX", ARGV[4])
if not reserved then
    return 0
end
redis.call("RPUSH", KEYS[2], ARGV[2])
return 1
"""

LEASE_JOB_SCRIPT = """
local removed = redis.call("LREM", KEYS[1], 1, ARGV[1])
if removed == 0 then
    return 0
end
redis.call("ZADD", KEYS[2], ARGV[2], ARGV[3])
return 1
"""


class ProcessingEntry(TypedDict):
    payload: str
    job: JobPayload


class JobHandler(Protocol):
    def __call__(self, job: JobPayload) -> None: ...


def matches_job_filters(
    job: JobPayload,
    *,
    workspace_id: UUID | None,
    job_type: JobType | str | None,
    resource_id: UUID | None,
) -> bool:
    if workspace_id is not None and job.workspace_id != workspace_id:
        return False
    if job_type is not None and job.job_type != JobType(job_type):
        return False
    return resource_id is None or job.resource_id == resource_id


@dataclass(frozen=True)
class RedisQueue:
    redis: Redis[str]
    keys: RedisKeyBuilder
    queue_name: str
    blocking_timeout_seconds: int = 1
    visibility_timeout_seconds: int = 900
    retry_base_delay_seconds: float = 0.0
    retry_max_delay_seconds: float = 300.0
    tracing_enabled: bool = True

    def enqueue(self, job: JobPayload, *, force: bool = False) -> bool:
        current_trace = current_trace_context()
        if self.tracing_enabled:
            with telemetry_span(
                "opsmesh.queue.enqueue",
                parent=current_trace,
                kind=SpanKind.PRODUCER,
                attributes={
                    "messaging.destination.name": self.queue_name,
                    "messaging.operation.name": "send",
                    "messaging.system": "redis",
                    "opsmesh.job.type": job.job_type.value,
                },
            ) as enqueue_trace:
                return self._enqueue(job.with_trace_context(enqueue_trace), force=force)
        return self._enqueue(job, force=force)

    def _enqueue(self, job: JobPayload, *, force: bool) -> bool:
        idempotency_key = self.keys.idempotency_key(str(job.workspace_id), job.idempotency_key)
        payload = self._serialize(job)
        queue_key = self.keys.queue(self.queue_name)
        queued = self.redis.eval(  # type: ignore[no-untyped-call]
            ENQUEUE_SCRIPT,
            2,
            idempotency_key,
            queue_key,
            str(job.job_id),
            payload,
            "1" if force else "0",
            "86400",
        )
        return bool(queued)

    def dequeue(self) -> JobPayload | None:
        return self._dequeue_with_optional_wait(lambda _: True)

    def dequeue_matching(
        self,
        predicate: Callable[[JobPayload], bool],
        *,
        scan_limit: int = 50,
    ) -> JobPayload | None:
        return self._dequeue_with_optional_wait(predicate, scan_limit=scan_limit)

    def ack(self, job: JobPayload) -> bool:
        return self._remove_processing_job(job.job_id) is not None

    @contextmanager
    def run_lock(self, workspace_id: str, run_id: str, ttl_seconds: int = 600) -> Iterator[bool]:
        lock_key = self.keys.run_lock(workspace_id, run_id)
        lock = self.redis.lock(lock_key, timeout=ttl_seconds, blocking=False)
        acquired = lock.acquire()
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def reclaim_expired(
        self, *, limit: int = 100, now: float | None = None
    ) -> list[JobPayload]:
        if limit <= 0:
            return []
        processing_key = self._processing_key()
        deadline = time.time() if now is None else now
        raw_entries = self.redis.zrangebyscore(
            processing_key,
            min="-inf",
            max=deadline,
            start=0,
            num=limit,
        )
        reclaimed: list[JobPayload] = []
        for raw_entry in raw_entries:
            entry = self._deserialize_processing_entry(raw_entry)
            removed = self.redis.zrem(processing_key, raw_entry)
            if int(removed) == 0:
                continue
            self.redis.rpush(self.keys.queue(self.queue_name), entry["payload"])
            reclaimed.append(entry["job"])
        return reclaimed

    def retry_or_dead_letter(
        self,
        job: JobPayload,
        *,
        error: BaseException | str | None = None,
        delay_seconds: float | None = None,
        now: float | None = None,
    ) -> None:
        self._remove_processing_job(job.job_id)
        next_job = job.next_attempt(error)
        if job.can_retry:
            retry_delay = self._retry_delay(job, delay_seconds=delay_seconds)
            if retry_delay <= 0:
                self.redis.rpush(self.keys.queue(self.queue_name), self._serialize(next_job))
            else:
                due_at = (time.time() if now is None else now) + retry_delay
                self.redis.zadd(self._retry_key(), {self._serialize(next_job): due_at})
            return

        self.redis.rpush(
            self.keys.dead_letter_queue(self.queue_name),
            self._serialize(next_job),
        )

    def reclaim_due_retries(
        self, *, limit: int = 100, now: float | None = None
    ) -> list[JobPayload]:
        if limit <= 0:
            return []
        deadline = time.time() if now is None else now
        raw_jobs = self.redis.zrangebyscore(
            self._retry_key(),
            min="-inf",
            max=deadline,
            start=0,
            num=limit,
        )
        reclaimed: list[JobPayload] = []
        for raw_job in raw_jobs:
            removed = self.redis.zrem(self._retry_key(), raw_job)
            if int(removed) == 0:
                continue
            self.redis.rpush(self.keys.queue(self.queue_name), raw_job)
            reclaimed.append(self._deserialize(raw_job))
        return reclaimed

    def list_dead_letters(
        self,
        limit: int = 50,
        *,
        workspace_id: UUID | None = None,
        job_type: JobType | str | None = None,
        resource_id: UUID | None = None,
    ) -> list[JobPayload]:
        return self._list_jobs(
            self.keys.dead_letter_queue(self.queue_name),
            limit=limit,
            workspace_id=workspace_id,
            job_type=job_type,
            resource_id=resource_id,
        )

    def count_queued(self, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.llen(self.keys.queue(self.queue_name)))
        return sum(
            1
            for raw_job in self.redis.lrange(self.keys.queue(self.queue_name), 0, -1)
            if self._deserialize(raw_job).workspace_id == workspace_id
        )

    def count_processing(self, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.zcard(self._processing_key()))
        return sum(
            1
            for raw_entry in self.redis.zrange(self._processing_key(), 0, -1)
            if self._deserialize_processing_entry(raw_entry)["job"].workspace_id == workspace_id
        )

    def count_scheduled_retries(self, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.zcard(self._retry_key()))
        return sum(
            1
            for raw_job in self.redis.zrange(self._retry_key(), 0, -1)
            if self._deserialize(raw_job).workspace_id == workspace_id
        )

    def list_scheduled_retries(
        self,
        limit: int = 50,
        *,
        workspace_id: UUID | None = None,
        job_type: JobType | str | None = None,
        resource_id: UUID | None = None,
    ) -> list[JobPayload]:
        return self._list_jobs(
            self._retry_key(),
            limit=limit,
            workspace_id=workspace_id,
            job_type=job_type,
            resource_id=resource_id,
            sorted_set=True,
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

    def list_queued(
        self,
        limit: int = 50,
        *,
        workspace_id: UUID | None = None,
        job_type: JobType | str | None = None,
        resource_id: UUID | None = None,
    ) -> list[JobPayload]:
        return self._list_jobs(
            self.keys.queue(self.queue_name),
            limit=limit,
            workspace_id=workspace_id,
            job_type=job_type,
            resource_id=resource_id,
        )

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

    def remove_queued_job(
        self,
        job_id: UUID,
        *,
        workspace_id: UUID | None = None,
        resource_id: UUID | None = None,
    ) -> JobPayload | None:
        queue_key = self.keys.queue(self.queue_name)
        for raw_job in self.redis.lrange(queue_key, 0, -1):
            job = self._deserialize(raw_job)
            if job.job_id != job_id:
                continue
            if workspace_id is not None and job.workspace_id != workspace_id:
                return None
            if resource_id is not None and job.resource_id != resource_id:
                return None
            removed = self.redis.lrem(queue_key, 1, raw_job)
            return job if int(removed) > 0 else None
        return None

    def _dequeue_with_optional_wait(
        self,
        predicate: Callable[[JobPayload], bool],
        *,
        scan_limit: int = 50,
    ) -> JobPayload | None:
        deadline = time.time() + max(0, self.blocking_timeout_seconds)
        while True:
            self.reclaim_due_retries()
            job = self._pop_best_matching(predicate, scan_limit=scan_limit)
            if job is not None or self.blocking_timeout_seconds <= 0 or time.time() >= deadline:
                return job
            time.sleep(min(0.05, max(0, deadline - time.time())))

    def _pop_best_matching(
        self,
        predicate: Callable[[JobPayload], bool],
        *,
        scan_limit: int = 50,
    ) -> JobPayload | None:
        queue_key = self.keys.queue(self.queue_name)
        best: tuple[int, int, bytes | str, JobPayload] | None = None
        raw_payloads = self.redis.lrange(queue_key, 0, max(1, scan_limit) - 1)
        for index, raw_payload in enumerate(raw_payloads):
            job = self._deserialize(raw_payload)
            if not predicate(job):
                continue
            candidate = (job.priority, -index, raw_payload, job)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is None:
            return None
        _, _, selected_payload, job = best
        return job if self._lease_raw_job(queue_key, selected_payload) else None

    def _lease_raw_job(self, queue_key: str, raw_payload: bytes | str) -> bool:
        processing_entry = self._serialize_processing_entry(raw_payload)
        processing_deadline = time.time() + self.visibility_timeout_seconds
        leased = self.redis.eval(  # type: ignore[no-untyped-call]
            LEASE_JOB_SCRIPT,
            2,
            queue_key,
            self._processing_key(),
            raw_payload,
            processing_deadline,
            processing_entry,
        )
        return bool(leased)

    def _remove_processing_job(self, job_id: UUID) -> JobPayload | None:
        for raw_entry in self.redis.zrange(self._processing_key(), 0, -1):
            entry = self._deserialize_processing_entry(raw_entry)
            job = entry["job"]
            if job.job_id != job_id:
                continue
            removed = self.redis.zrem(self._processing_key(), raw_entry)
            return job if int(removed) > 0 else None
        return None

    def _retry_delay(self, job: JobPayload, *, delay_seconds: float | None) -> float:
        if delay_seconds is not None:
            return max(0.0, delay_seconds)
        if self.retry_base_delay_seconds <= 0:
            return 0.0
        delay = float(self.retry_base_delay_seconds * (2 ** max(0, job.attempt)))
        if self.retry_max_delay_seconds <= 0:
            return delay
        return min(delay, self.retry_max_delay_seconds)

    def _list_jobs(
        self,
        key: str,
        *,
        limit: int,
        workspace_id: UUID | None,
        job_type: JobType | str | None,
        resource_id: UUID | None,
        sorted_set: bool = False,
    ) -> list[JobPayload]:
        if limit <= 0:
            return []
        raw_jobs = self.redis.zrange(key, 0, -1) if sorted_set else self.redis.lrange(key, 0, -1)
        jobs: list[JobPayload] = []
        for raw_job in raw_jobs:
            job = self._deserialize(raw_job)
            if not matches_job_filters(
                job,
                workspace_id=workspace_id,
                job_type=job_type,
                resource_id=resource_id,
            ):
                continue
            jobs.append(job)
            if len(jobs) >= limit:
                break
        return jobs

    def _serialize(self, job: JobPayload) -> str:
        return job.model_dump_json()

    def _deserialize(self, raw_payload: bytes | str) -> JobPayload:
        if isinstance(raw_payload, bytes):
            raw_payload = raw_payload.decode("utf-8")
        return JobPayload.model_validate(json.loads(raw_payload))

    def _processing_key(self) -> str:
        return f"{self.keys.queue(self.queue_name)}:processing"

    def _retry_key(self) -> str:
        return f"{self.keys.queue(self.queue_name)}:retry"

    def _serialize_processing_entry(self, raw_payload: bytes | str) -> str:
        payload = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else raw_payload
        return json.dumps(
            {"lease_id": str(uuid4()), "payload": payload},
            separators=(",", ":"),
        )

    def _deserialize_processing_entry(self, raw_entry: bytes | str) -> ProcessingEntry:
        if isinstance(raw_entry, bytes):
            raw_entry = raw_entry.decode("utf-8")
        entry = json.loads(raw_entry)
        payload = entry["payload"]
        return {"payload": payload, "job": self._deserialize(payload)}


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

