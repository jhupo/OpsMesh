from __future__ import annotations

import time

from backend.app.workers.jobs import JobPayload


class QueueRetryMixin:
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
        self,
        *,
        limit: int = 100,
        now: float | None = None,
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

    def _retry_delay(self, job: JobPayload, *, delay_seconds: float | None) -> float:
        if delay_seconds is not None:
            return max(0.0, delay_seconds)
        if self.retry_base_delay_seconds <= 0:
            return 0.0
        delay = self.retry_base_delay_seconds * (2 ** max(0, job.attempt))
        if self.retry_max_delay_seconds <= 0:
            return delay
        return min(delay, self.retry_max_delay_seconds)
