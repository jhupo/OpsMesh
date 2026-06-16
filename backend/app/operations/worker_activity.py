from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    OperationsWorkerLifecycleResponse,
    WorkerLifecycleBucketResponse,
)
from backend.app.core.typing import string_list
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.operations.utils import ensure_aware_utc
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue

TERMINAL_LEASE_STATUSES = {"completed", "failed", "expired"}


class WorkerLifecycleActivityService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder

    def worker_lifecycle_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
    ) -> OperationsWorkerLifecycleResponse:
        now = datetime.now(UTC)
        buckets: dict[str, dict[str, int | list[int] | None]] = {}
        if self._redis is not None:
            queue = RedisQueue(self._redis, self._keys, queue_name)
            for job in queue.peek(limit=1_000):
                if job.workspace_id != workspace_id:
                    continue
                age_seconds = max(
                    0,
                    int((now - ensure_aware_utc(job.created_at)).total_seconds()),
                )
                for worker_type in job_worker_types(job):
                    bucket = worker_lifecycle_bucket(buckets, worker_type)
                    bucket["queued_jobs"] = int(bucket["queued_jobs"]) + 1
                    bucket["oldest_queued_age_seconds"] = max_optional_int(
                        bucket.get("oldest_queued_age_seconds"),
                        age_seconds,
                    )

        nodes_by_worker_id = {
            node.worker_id: node for node in self._session.scalars(select(WorkerNode)).all()
        }
        leases = self._session.scalars(
            select(WorkerLease).where(WorkerLease.workspace_id == workspace_id)
        ).all()
        for lease in leases:
            worker_type = lease_worker_type(lease, nodes_by_worker_id)
            bucket = worker_lifecycle_bucket(buckets, worker_type)
            if lease.status == "running":
                bucket["running_jobs"] = int(bucket["running_jobs"]) + 1
                running_age_seconds = max(
                    0,
                    int((now - ensure_aware_utc(lease.started_at)).total_seconds()),
                )
                bucket["oldest_running_age_seconds"] = max_optional_int(
                    bucket.get("oldest_running_age_seconds"),
                    running_age_seconds,
                )
                continue
            if lease.status == "completed":
                bucket["completed_jobs"] = int(bucket["completed_jobs"]) + 1
            elif lease.status == "failed":
                bucket["failed_jobs"] = int(bucket["failed_jobs"]) + 1
            elif lease.status == "retrying":
                bucket["retried_jobs"] = int(bucket["retried_jobs"]) + 1
            elif lease.status == "expired":
                bucket["expired_jobs"] = int(bucket["expired_jobs"]) + 1
            if lease.status in TERMINAL_LEASE_STATUSES and lease.finished_at is not None:
                durations = bucket["durations"]
                if isinstance(durations, list):
                    durations.append(
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

        return OperationsWorkerLifecycleResponse(
            generated_at=now,
            queue_name=queue_name,
            worker_types=[
                worker_lifecycle_response(worker_type, values)
                for worker_type, values in sorted(buckets.items())
            ],
        )


def job_worker_types(job: JobPayload) -> list[str]:
    worker_types = string_list(job.routing.get("worker_types"))
    return worker_types or ["unrouted"]


def lease_worker_type(lease: WorkerLease, nodes_by_worker_id: dict[str, WorkerNode]) -> str:
    node = nodes_by_worker_id.get(lease.worker_id)
    if node is not None:
        return node.worker_type
    metadata_worker_type = lease.lease_metadata.get("worker_type")
    return metadata_worker_type if isinstance(metadata_worker_type, str) else "unknown"


def worker_lifecycle_bucket(
    buckets: dict[str, dict[str, int | list[int] | None]],
    worker_type: str,
) -> dict[str, int | list[int] | None]:
    return buckets.setdefault(
        worker_type,
        {
            "queued_jobs": 0,
            "running_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "retried_jobs": 0,
            "expired_jobs": 0,
            "oldest_queued_age_seconds": None,
            "oldest_running_age_seconds": None,
            "durations": [],
        },
    )


def worker_lifecycle_response(
    worker_type: str,
    values: dict[str, int | list[int] | None],
) -> WorkerLifecycleBucketResponse:
    terminal_jobs = (
        int(values["completed_jobs"]) + int(values["failed_jobs"]) + int(values["expired_jobs"])
    )
    unsuccessful_jobs = int(values["failed_jobs"]) + int(values["expired_jobs"])
    durations = values["durations"]
    duration_values = durations if isinstance(durations, list) else []
    return WorkerLifecycleBucketResponse(
        worker_type=worker_type,
        queued_jobs=int(values["queued_jobs"]),
        running_jobs=int(values["running_jobs"]),
        completed_jobs=int(values["completed_jobs"]),
        failed_jobs=int(values["failed_jobs"]),
        retried_jobs=int(values["retried_jobs"]),
        expired_jobs=int(values["expired_jobs"]),
        failure_rate=round(unsuccessful_jobs / terminal_jobs, 4) if terminal_jobs > 0 else 0.0,
        average_duration_seconds=int(sum(duration_values) / len(duration_values))
        if duration_values
        else None,
        oldest_queued_age_seconds=cast(int | None, values["oldest_queued_age_seconds"]),
        oldest_running_age_seconds=cast(int | None, values["oldest_running_age_seconds"]),
    )


def max_optional_int(current: object, candidate: int) -> int:
    return candidate if not isinstance(current, int) else max(current, candidate)
