from __future__ import annotations

import time
from collections.abc import Callable
from uuid import UUID

from redis.exceptions import ResponseError

from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue.scripts import LEASE_JOB_SCRIPT, eval_unsupported


class QueueLeaseMixin:
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
            removed = self.redis.zrem(processing_key, raw_entry)
            if int(removed) == 0:
                continue
            self.redis.rpush(self.keys.queue(self.queue_name), entry["payload"])
            reclaimed.append(entry["job"])
        return reclaimed

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
        _, _, raw_payload, job = best
        return job if self._lease_raw_job(queue_key, raw_payload) else None

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

    def _lease_raw_job(self, queue_key: str, raw_payload: bytes | str) -> bool:
        processing_entry = self._serialize_processing_entry(raw_payload)
        processing_deadline = time.time() + self.visibility_timeout_seconds
        try:
            leased = self.redis.eval(
                LEASE_JOB_SCRIPT,
                2,
                queue_key,
                self._processing_key(),
                raw_payload,
                processing_deadline,
                processing_entry,
            )
        except ResponseError as exc:
            if not eval_unsupported(exc):
                raise
            removed = self.redis.lrem(queue_key, 1, raw_payload)
            if int(removed) == 0:
                return False
            self.redis.zadd(self._processing_key(), {processing_entry: processing_deadline})
            return True
        return bool(leased)

    def _remove_processing_job(self, job_id: UUID) -> JobPayload | None:
        for raw_entry in self.redis.zrange(self._processing_key(), 0, -1):
            entry = self._deserialize_processing_entry(raw_entry)
            job = entry["job"]
            if not isinstance(job, JobPayload) or job.job_id != job_id:
                continue
            removed = self.redis.zrem(self._processing_key(), raw_entry)
            return job if int(removed) > 0 else None
        return None
