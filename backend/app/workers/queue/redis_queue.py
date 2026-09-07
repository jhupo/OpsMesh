from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from opentelemetry.trace import SpanKind
from redis import Redis
from redis.exceptions import ResponseError

from backend.app.core.trace_context import current_trace_context, telemetry_span
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.redis.locks import redis_lock
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue.leases import QueueLeaseMixin
from backend.app.workers.queue.queries import QueueInspectionMixin
from backend.app.workers.queue.retries import QueueRetryMixin
from backend.app.workers.queue.scripts import ENQUEUE_SCRIPT, eval_unsupported
from backend.app.workers.queue.serialization import QueueSerializationMixin


@dataclass(frozen=True)
class RedisQueue(
    QueueSerializationMixin,
    QueueLeaseMixin,
    QueueRetryMixin,
    QueueInspectionMixin,
):
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
        try:
            queued = self.redis.eval(
                ENQUEUE_SCRIPT,
                2,
                idempotency_key,
                queue_key,
                str(job.job_id),
                payload,
                "1" if force else "0",
                "86400",
            )
        except ResponseError as exc:
            if not eval_unsupported(exc):
                raise
            return self._enqueue_without_lua(
                idempotency_key=idempotency_key,
                queue_key=queue_key,
                job_id=str(job.job_id),
                payload=payload,
                force=force,
            )
        return bool(queued)

    def _enqueue_without_lua(
        self,
        *,
        idempotency_key: str,
        queue_key: str,
        job_id: str,
        payload: str,
        force: bool,
    ) -> bool:
        if force:
            self.redis.set(idempotency_key, job_id, ex=86_400)
        else:
            created = self.redis.set(idempotency_key, job_id, nx=True, ex=86_400)
            if not created:
                return False
        self.redis.rpush(queue_key, payload)
        return True

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
        with redis_lock(self.redis, lock_key, ttl_seconds) as acquired:
            yield acquired
