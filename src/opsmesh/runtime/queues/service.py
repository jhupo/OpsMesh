"""Redis-backed worker queue and its lease/retry lifecycle.

The queue is intentionally one cohesive module.  Enqueueing, leasing, retrying and inspection
share the same serialization and Redis-key invariants, so splitting each operation into mixins
made the ownership and failure semantics harder to follow without providing a real extension
boundary.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, TypedDict
from uuid import UUID, uuid4

from opentelemetry.trace import SpanKind
from redis import Redis

from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.queues.run_lock import renewable_run_lock
from opsmesh.shared.redis.keys import RedisKeyBuilder
from opsmesh.shared.telemetry.request_context import request_id_var
from opsmesh.shared.telemetry.trace_context import current_trace_context, telemetry_span

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

ENQUEUE_ACTIVE_ONLY_SCRIPT = """
local candidate = cjson.decode(ARGV[1])
local function matches(raw, processing)
    local entry = cjson.decode(raw)
    local job = entry
    if processing then job = cjson.decode(entry.payload) end
    return job.workspace_id == candidate.workspace_id
        and job.idempotency_key == candidate.idempotency_key
end
for _, raw in ipairs(redis.call("LRANGE", KEYS[1], 0, -1)) do
    if matches(raw, false) then return 0 end
end
for _, raw in ipairs(redis.call("ZRANGE", KEYS[2], 0, -1)) do
    if matches(raw, true) then return 0 end
end
for _, raw in ipairs(redis.call("ZRANGE", KEYS[3], 0, -1)) do
    if matches(raw, false) then return 0 end
end
redis.call("RPUSH", KEYS[1], ARGV[1])
return 1
"""

SCAN_CANDIDATES_SCRIPT = """
local count = redis.call("LLEN", KEYS[1])
if count == 0 then return {} end
local limit = tonumber(ARGV[1])
local pages = math.ceil(count / limit)
local sequence = redis.call("INCR", KEYS[2]) - 1
redis.call("EXPIRE", KEYS[2], 3600)
local first = (sequence % pages) * limit
return redis.call("LRANGE", KEYS[1], first, first + limit - 1)
"""

LEASE_JOB_SCRIPT = """
local candidates = cjson.decode(ARGV[1])
local best = nil
local best_priority = nil
local best_score = nil
for _, candidate in ipairs(candidates) do
    if redis.call("LPOS", KEYS[1], candidate.payload) ~= false then
        local job = cjson.decode(candidate.payload)
        local score = tonumber(redis.call("ZSCORE", KEYS[3], job.workspace_id)) or 0
        local priority = tonumber(job.priority) or 0
        if best == nil or priority > best_priority
            or (priority == best_priority and score < best_score) then
            best = candidate
            best_priority = priority
            best_score = score
        end
    end
end
if best == nil then return false end
redis.call("LREM", KEYS[1], 1, best.payload)
redis.call("ZADD", KEYS[2], ARGV[2], best.entry)
local job = cjson.decode(best.payload)
local sequence = redis.call("INCR", KEYS[4])
redis.call("ZADD", KEYS[3], sequence, job.workspace_id)
redis.call("ZREMRANGEBYRANK", KEYS[3], 0, -1001)
return best.entry
"""

RECLAIM_PROCESSING_SCRIPT = """
if redis.call("ZREM", KEYS[1], ARGV[1]) == 0 then
    return 0
end
redis.call("RPUSH", KEYS[2], ARGV[2])
return 1
"""

RECLAIM_RETRY_SCRIPT = """
if redis.call("ZREM", KEYS[1], ARGV[1]) == 0 then
    return 0
end
redis.call("RPUSH", KEYS[2], ARGV[1])
return 1
"""

RETRY_OR_DEAD_LETTER_SCRIPT = """
if redis.call("ZREM", KEYS[1], ARGV[1]) == 0 then
    return 0
end
if ARGV[4] == "retry" then
    if tonumber(ARGV[3]) <= 0 then
        redis.call("RPUSH", KEYS[2], ARGV[2])
    else
        redis.call("ZADD", KEYS[3], ARGV[3], ARGV[2])
    end
else
    redis.call("RPUSH", KEYS[4], ARGV[2])
end
return 1
"""

REQUEUE_DEAD_LETTER_SCRIPT = """
if redis.call("LREM", KEYS[1], 1, ARGV[1]) == 0 then
    return 0
end
redis.call("RPUSH", KEYS[2], ARGV[2])
return 1
"""

HEARTBEAT_PROCESSING_SCRIPT = """
if redis.call("ZSCORE", KEYS[1], ARGV[1]) == false then
    return 0
end
redis.call("ZADD", KEYS[1], ARGV[2], ARGV[1])
return 1
"""


class ProcessingEntry(TypedDict):
    lease_id: str
    payload: str
    job: JobPayload


class JobHandler(Protocol):
    def __call__(self, job: JobPayload) -> None: ...


@dataclass(frozen=True, slots=True)
class QueueLease:
    job: JobPayload
    lease_token: str


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
        if current_trace is not None or request_id_var.get() is not None:
            job = job.with_trace_context()
        return self._enqueue(job, force=force)

    def ensure_enqueued(self, job: JobPayload) -> bool:
        """Restore a durable job only when no active Redis projection exists.

        Recovery must not use ``force=True`` because that creates a second delivery when the
        original payload is still queued, processing, or waiting for retry.  The idempotency key
        is deliberately cleared only after all active projections have been inspected; a stale
        key left behind by a Redis restart can then be repaired safely.
        """

        if self._active_projection_exists(job):
            return False
        self.redis.delete(self.keys.idempotency_key(str(job.workspace_id), job.idempotency_key))
        return self.enqueue(job)

    def enqueue_runtime_transition(self, job: JobPayload) -> bool:
        """Coalesce active start/stop requests atomically; completed requests are reusable."""
        if job.job_type != JobType.RUNTIME_CONTROL or job.routing.get("action") not in {
            "start",
            "stop",
        }:
            raise ValueError("Only runtime start/stop jobs support reusable deduplication")
        return self.enqueue_coalesced(job)

    def enqueue_coalesced(self, job: JobPayload) -> bool:
        """Keep one queued/processing/retrying projection for a resource operation."""
        job = job.with_trace_context()
        return bool(
            self.redis.eval(  # type: ignore[no-untyped-call]
                ENQUEUE_ACTIVE_ONLY_SCRIPT,
                3,
                self.keys.queue(self.queue_name),
                self._processing_key(),
                self._retry_key(),
                self._serialize(job),
            )
        )

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
        lease = self.dequeue_with_lease()
        return lease.job if lease is not None else None

    def dequeue_with_lease(self) -> QueueLease | None:
        return self._dequeue_with_optional_wait(lambda _: True)

    def dequeue_matching(
        self,
        predicate: Callable[[JobPayload], bool],
        *,
        scan_limit: int = 50,
    ) -> JobPayload | None:
        lease = self.dequeue_matching_with_lease(predicate, scan_limit=scan_limit)
        return lease.job if lease is not None else None

    def dequeue_matching_with_lease(
        self,
        predicate: Callable[[JobPayload], bool],
        *,
        scan_limit: int = 50,
        wait: bool = True,
    ) -> QueueLease | None:
        if not wait:
            self.reclaim_due_retries()
            return self._pop_best_matching(predicate, scan_limit=scan_limit)
        return self._dequeue_with_optional_wait(predicate, scan_limit=scan_limit)

    def ack(self, job: JobPayload, *, lease_token: str | None = None) -> bool:
        return self._remove_processing_job(job.job_id, lease_token=lease_token) is not None

    def heartbeat(
        self,
        job: JobPayload,
        *,
        lease_token: str,
        visibility_timeout_seconds: int | None = None,
        now: float | None = None,
    ) -> bool:
        processing_entry = self._processing_entry_for(job.job_id, lease_token=lease_token)
        if processing_entry is None:
            return False
        raw_entry = processing_entry[1]
        deadline = (time.time() if now is None else now) + (
            self.visibility_timeout_seconds
            if visibility_timeout_seconds is None
            else visibility_timeout_seconds
        )
        refreshed = self.redis.eval(  # type: ignore[no-untyped-call]
            HEARTBEAT_PROCESSING_SCRIPT,
            1,
            self._processing_key(),
            raw_entry,
            deadline,
        )
        return bool(refreshed)

    def processing_lease_token(self, job_id: UUID) -> str | None:
        entry = self._processing_entry_for(job_id)
        return entry[0] if entry is not None else None

    @contextmanager
    def run_lock(self, workspace_id: str, run_id: str, ttl_seconds: int = 600) -> Iterator[bool]:
        lock_key = self.keys.run_lock(workspace_id, run_id)
        with renewable_run_lock(self.redis, lock_key, ttl_seconds) as acquired:
            yield acquired

    def reclaim_expired(self, *, limit: int = 100, now: float | None = None) -> list[JobPayload]:
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
            moved = self.redis.eval(  # type: ignore[no-untyped-call]
                RECLAIM_PROCESSING_SCRIPT,
                2,
                processing_key,
                self.keys.queue(self.queue_name),
                raw_entry,
                entry["payload"],
            )
            if not moved:
                continue
            reclaimed.append(entry["job"])
        return reclaimed

    def retry_or_dead_letter(
        self,
        job: JobPayload,
        *,
        error: BaseException | str | None = None,
        delay_seconds: float | None = None,
        now: float | None = None,
        lease_token: str | None = None,
    ) -> bool:
        next_job = job.next_attempt(error)
        serialized = self._serialize(next_job)
        retry_delay = self._retry_delay(job, delay_seconds=delay_seconds)
        due_at = (
            (time.time() if now is None else now) + retry_delay
            if job.can_retry and retry_delay > 0
            else 0.0
        )

        if lease_token is None:
            if job.can_retry:
                if due_at > 0:
                    self.redis.zadd(self._retry_key(), {serialized: due_at})
                else:
                    self.redis.rpush(self.keys.queue(self.queue_name), serialized)
            else:
                self.redis.rpush(self.keys.dead_letter_queue(self.queue_name), serialized)
            return True

        processing_entry = self._processing_entry_for(job.job_id, lease_token=lease_token)
        if processing_entry is None:
            return False
        moved = self.redis.eval(  # type: ignore[no-untyped-call]
            RETRY_OR_DEAD_LETTER_SCRIPT,
            4,
            self._processing_key(),
            self.keys.queue(self.queue_name),
            self._retry_key(),
            self.keys.dead_letter_queue(self.queue_name),
            processing_entry[1],
            serialized,
            due_at,
            "retry" if job.can_retry else "dead",
        )
        return bool(moved)

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
            moved = self.redis.eval(  # type: ignore[no-untyped-call]
                RECLAIM_RETRY_SCRIPT,
                2,
                self._retry_key(),
                self.keys.queue(self.queue_name),
                raw_job,
            )
            if not moved:
                continue
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

    def queued_job_resource_ids(self, *, job_type: JobType | None = None) -> set[UUID]:
        return self._resource_ids_from_jobs(
            self.redis.lrange(self.keys.queue(self.queue_name), 0, -1),
            job_type=job_type,
        )

    def processing_job_resource_ids(self, *, job_type: JobType | None = None) -> set[UUID]:
        return self._resource_ids_from_jobs(
            [
                self._deserialize_processing_entry(raw_entry)["payload"]
                for raw_entry in self.redis.zrange(self._processing_key(), 0, -1)
            ],
            job_type=job_type,
        )

    def scheduled_retry_job_resource_ids(self, *, job_type: JobType | None = None) -> set[UUID]:
        return self._resource_ids_from_jobs(
            self.redis.zrange(self._retry_key(), 0, -1),
            job_type=job_type,
        )

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
            update = {"job_id": uuid4(), "attempt": 0} if reset_attempts else {"job_id": uuid4()}
            retry_job = job.model_copy(update=update)
            moved = self.redis.eval(  # type: ignore[no-untyped-call]
                REQUEUE_DEAD_LETTER_SCRIPT,
                2,
                dead_letter_key,
                self.keys.queue(self.queue_name),
                raw_job,
                self._serialize(retry_job),
            )
            return retry_job if moved else None
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
    ) -> QueueLease | None:
        deadline = time.time() + max(0, self.blocking_timeout_seconds)
        while True:
            self.reclaim_due_retries()
            lease = self._pop_best_matching(predicate, scan_limit=scan_limit)
            if lease is not None or self.blocking_timeout_seconds <= 0 or time.time() >= deadline:
                return lease
            time.sleep(min(0.05, max(0, deadline - time.time())))

    def _active_projection_exists(self, job: JobPayload) -> bool:
        for raw_job in self.redis.lrange(self.keys.queue(self.queue_name), 0, -1):
            if self._deserialize(raw_job).idempotency_key == job.idempotency_key:
                return True
        for raw_entry in self.redis.zrange(self._processing_key(), 0, -1):
            processing_job = self._deserialize_processing_entry(raw_entry)["job"]
            if processing_job.idempotency_key == job.idempotency_key:
                return True
        for raw_job in self.redis.zrange(self._retry_key(), 0, -1):
            if self._deserialize(raw_job).idempotency_key == job.idempotency_key:
                return True
        return False

    def _pop_best_matching(
        self,
        predicate: Callable[[JobPayload], bool],
        *,
        scan_limit: int = 50,
    ) -> QueueLease | None:
        queue_key = self.keys.queue(self.queue_name)
        candidates: list[dict[str, str]] = []
        raw_payloads = self.redis.eval(  # type: ignore[no-untyped-call]
            SCAN_CANDIDATES_SCRIPT,
            2,
            queue_key,
            self.keys.queue_scan_sequence(self.queue_name),
            max(1, scan_limit),
        )
        for raw_payload in raw_payloads:
            job = self._deserialize(raw_payload)
            if not predicate(job):
                continue
            payload = raw_payload.decode() if isinstance(raw_payload, bytes) else raw_payload
            candidates.append(
                {"payload": payload, "entry": self._serialize_processing_entry(payload)}
            )
        if not candidates:
            return None
        processing_deadline = time.time() + self.visibility_timeout_seconds
        selected = self.redis.eval(  # type: ignore[no-untyped-call]
            LEASE_JOB_SCRIPT,
            4,
            queue_key,
            self._processing_key(),
            self.keys.queue_fairness(self.queue_name),
            self.keys.queue_fairness_sequence(self.queue_name),
            json.dumps(candidates),
            processing_deadline,
        )
        if not isinstance(selected, bytes | str):
            return None
        entry = self._deserialize_processing_entry(selected)
        return QueueLease(job=entry["job"], lease_token=entry["lease_id"])

    def _remove_processing_job(
        self,
        job_id: UUID,
        *,
        lease_token: str | None = None,
    ) -> JobPayload | None:
        raw_entry = self._processing_entry_for(job_id, lease_token=lease_token)
        if raw_entry is None:
            return None
        removed = self.redis.zrem(self._processing_key(), raw_entry[1])
        return raw_entry[2]["job"] if int(removed) > 0 else None

    def _processing_entry_for(
        self,
        job_id: UUID,
        *,
        lease_token: str | None = None,
    ) -> tuple[str, str, ProcessingEntry] | None:
        for raw_entry in self.redis.zrange(self._processing_key(), 0, -1):
            entry = self._deserialize_processing_entry(raw_entry)
            if entry["job"].job_id != job_id:
                continue
            if lease_token is not None and entry["lease_id"] != lease_token:
                continue
            return entry["lease_id"], raw_entry, entry
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

    def _resource_ids_from_jobs(
        self,
        raw_jobs: Sequence[bytes | str | JobPayload],
        *,
        job_type: JobType | None,
    ) -> set[UUID]:
        resource_ids: set[UUID] = set()
        for raw_job in raw_jobs:
            job = raw_job if isinstance(raw_job, JobPayload) else self._deserialize(raw_job)
            if job_type is not None and job.job_type != job_type:
                continue
            resource_ids.add(job.resource_id)
        return resource_ids

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
        return {
            "lease_id": entry["lease_id"],
            "payload": payload,
            "job": self._deserialize(payload),
        }
