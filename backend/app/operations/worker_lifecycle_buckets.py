from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from backend.app.api.schemas.operation_capacity import WorkerLifecycleBucketResponse
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.operations.utils import age_seconds, ensure_aware_utc
from backend.app.operations.worker_lifecycle_queue import job_worker_types
from backend.app.workers.jobs import JobPayload

TERMINAL_LEASE_STATUSES = {"completed", "failed", "expired"}


@dataclass(slots=True)
class WorkerLifecycleBucket:
    worker_type: str
    queued_jobs: int = 0
    running_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    retried_jobs: int = 0
    expired_jobs: int = 0
    oldest_queued_age_seconds: int | None = None
    oldest_running_age_seconds: int | None = None
    durations: list[int] = field(default_factory=list)

    def add_queued_job(self, job: JobPayload, now: datetime) -> None:
        self.queued_jobs += 1
        self.oldest_queued_age_seconds = _max_optional(
            self.oldest_queued_age_seconds,
            age_seconds(now, job.created_at) or 0,
        )

    def add_lease(self, lease: WorkerLease, now: datetime) -> None:
        if lease.status == "running":
            self.running_jobs += 1
            self.oldest_running_age_seconds = _max_optional(
                self.oldest_running_age_seconds,
                age_seconds(now, lease.started_at) or 0,
            )
            return
        if lease.status == "completed":
            self.completed_jobs += 1
        elif lease.status == "failed":
            self.failed_jobs += 1
        elif lease.status == "retrying":
            self.retried_jobs += 1
        elif lease.status == "expired":
            self.expired_jobs += 1

        if lease.status in TERMINAL_LEASE_STATUSES and lease.finished_at is not None:
            self.durations.append(
                max(
                    0,
                    int(
                        (
                            ensure_aware_utc(lease.finished_at)
                            - ensure_aware_utc(lease.started_at)
                        ).total_seconds()
                    ),
                )
            )

    def response(self) -> WorkerLifecycleBucketResponse:
        terminal_jobs = self.completed_jobs + self.failed_jobs + self.expired_jobs
        unsuccessful_jobs = self.failed_jobs + self.expired_jobs
        return WorkerLifecycleBucketResponse(
            worker_type=self.worker_type,
            queued_jobs=self.queued_jobs,
            running_jobs=self.running_jobs,
            completed_jobs=self.completed_jobs,
            failed_jobs=self.failed_jobs,
            retried_jobs=self.retried_jobs,
            expired_jobs=self.expired_jobs,
            failure_rate=round(unsuccessful_jobs / terminal_jobs, 4)
            if terminal_jobs > 0
            else 0.0,
            average_duration_seconds=int(sum(self.durations) / len(self.durations))
            if self.durations
            else None,
            oldest_queued_age_seconds=self.oldest_queued_age_seconds,
            oldest_running_age_seconds=self.oldest_running_age_seconds,
        )


class WorkerLifecycleBucketAccumulator:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self._buckets: dict[str, WorkerLifecycleBucket] = {}

    def add_queued_job(self, job: JobPayload) -> None:
        for worker_type in job_worker_types(job):
            self._bucket(worker_type).add_queued_job(job, self._now)

    def add_lease(
        self,
        lease: WorkerLease,
        nodes_by_worker_id: dict[str, WorkerNode],
    ) -> None:
        self._bucket(lease_worker_type(lease, nodes_by_worker_id)).add_lease(lease, self._now)

    def responses(self) -> list[WorkerLifecycleBucketResponse]:
        return [bucket.response() for _, bucket in sorted(self._buckets.items())]

    def _bucket(self, worker_type: str) -> WorkerLifecycleBucket:
        return self._buckets.setdefault(worker_type, WorkerLifecycleBucket(worker_type))


def lease_worker_type(lease: WorkerLease, nodes_by_worker_id: dict[str, WorkerNode]) -> str:
    node = nodes_by_worker_id.get(lease.worker_id)
    if node is not None:
        return node.worker_type
    metadata_worker_type = lease.lease_metadata.get("worker_type")
    return metadata_worker_type if isinstance(metadata_worker_type, str) else "unknown"


def _max_optional(current: int | None, candidate: int) -> int:
    return candidate if current is None else max(current, candidate)
