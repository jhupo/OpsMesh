from __future__ import annotations

from uuid import UUID, uuid4

from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.contracts import QueueStorage
from backend.app.workers.queue.filters import matches_job_filters


class QueueInspectionMixin:
    def list_dead_letters(
        self: QueueStorage,
        limit: int = 50,
        *,
        workspace_id: UUID | None = None,
        job_type: JobType | str | None = None,
        resource_id: UUID | None = None,
    ) -> list[JobPayload]:
        jobs: list[JobPayload] = []
        for raw_job in self.redis.lrange(self.keys.dead_letter_queue(self.queue_name), 0, -1):
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

    def count_queued(self: QueueStorage, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.llen(self.keys.queue(self.queue_name)))
        return sum(
            1
            for raw_job in self.redis.lrange(self.keys.queue(self.queue_name), 0, -1)
            if self._deserialize(raw_job).workspace_id == workspace_id
        )

    def count_processing(self: QueueStorage, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.zcard(self._processing_key()))
        return sum(
            1
            for raw_entry in self.redis.zrange(self._processing_key(), 0, -1)
            if self._deserialize_processing_entry(raw_entry)["job"].workspace_id == workspace_id
        )

    def count_scheduled_retries(self: QueueStorage, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.zcard(self._retry_key()))
        return sum(
            1
            for raw_job in self.redis.zrange(self._retry_key(), 0, -1)
            if self._deserialize(raw_job).workspace_id == workspace_id
        )

    def list_scheduled_retries(
        self: QueueStorage,
        limit: int = 50,
        *,
        workspace_id: UUID | None = None,
        job_type: JobType | str | None = None,
        resource_id: UUID | None = None,
    ) -> list[JobPayload]:
        if limit <= 0:
            return []
        jobs: list[JobPayload] = []
        for raw_job in self.redis.zrange(self._retry_key(), 0, -1):
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

    def count_dead_letters(self: QueueStorage, *, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return int(self.redis.llen(self.keys.dead_letter_queue(self.queue_name)))
        return sum(
            1
            for raw_job in self.redis.lrange(self.keys.dead_letter_queue(self.queue_name), 0, -1)
            if self._deserialize(raw_job).workspace_id == workspace_id
        )

    def peek(self: QueueStorage, *, limit: int = 50) -> list[JobPayload]:
        if limit <= 0:
            return []
        return [
            self._deserialize(raw_job)
            for raw_job in self.redis.lrange(self.keys.queue(self.queue_name), 0, limit - 1)
        ]

    def list_queued(
        self: QueueStorage,
        limit: int = 50,
        *,
        workspace_id: UUID | None = None,
        job_type: JobType | str | None = None,
        resource_id: UUID | None = None,
    ) -> list[JobPayload]:
        if limit <= 0:
            return []
        jobs: list[JobPayload] = []
        for raw_job in self.redis.lrange(self.keys.queue(self.queue_name), 0, -1):
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

    def requeue_dead_letter(
        self: QueueStorage,
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
        self: QueueStorage,
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
