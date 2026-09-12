from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_capacity import OperationsWorkerLifecycleResponse
from backend.app.execution.operations.models import WorkerLease, WorkerNode
from backend.app.execution.operations.worker_lifecycle_buckets import (
    WorkerLifecycleBucketAccumulator,
)
from backend.app.execution.workers.jobs import JobPayload
from backend.app.execution.workers.redis_queue import RedisQueue
from backend.app.redis.keys import RedisKeyBuilder

RUNNING_LEASE_STATUSES = {"running"}
LIFECYCLE_EVENTS_LIMIT = 50


def append_worker_lifecycle_events(
    metadata: dict[str, object],
    events: list[dict[str, object]],
) -> dict[str, object]:
    existing = metadata.get("lifecycle_events")
    lifecycle_events = list(existing) if isinstance(existing, list) else []
    lifecycle_events.extend(events)
    metadata["lifecycle_events"] = lifecycle_events[-LIFECYCLE_EVENTS_LIMIT:]
    if events:
        metadata["last_lifecycle_event"] = events[-1]
    return metadata


def worker_lifecycle_event(
    event_type: str,
    at: datetime,
    *,
    attempt: int,
    status: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": event_type,
        "at": at.isoformat(),
        "attempt": attempt,
    }
    if status is not None:
        event["status"] = status
    if metadata:
        event.update(metadata)
    return event


def worker_finish_lifecycle_event(status: str) -> str:
    if status == "retrying":
        return "requeued"
    if status == "failed":
        return "failed"
    if status == "expired":
        return "expired"
    return "completed"


class WorkerLifecyclePayloadService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._queries = WorkerLifecycleQueryService(session)
        self._queue = WorkerLifecycleQueueReader(redis, key_builder)

    def worker_lifecycle_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
    ) -> OperationsWorkerLifecycleResponse:
        now = datetime.now(UTC)
        buckets = WorkerLifecycleBucketAccumulator(now)
        for job in self._queue.queued_jobs(workspace_id=workspace_id, queue_name=queue_name):
            buckets.add_queued_job(job)
        nodes_by_worker_id = self._queries.worker_nodes_by_worker_id()
        for lease in self._queries.worker_leases(workspace_id):
            buckets.add_lease(lease, nodes_by_worker_id)
        return OperationsWorkerLifecycleResponse(
            generated_at=now,
            queue_name=queue_name,
            worker_types=buckets.responses(),
        )


class WorkerLifecycleQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def worker_nodes_by_worker_id(self) -> dict[str, WorkerNode]:
        return {node.worker_id: node for node in self._session.scalars(select(WorkerNode)).all()}

    def worker_leases(self, workspace_id: UUID) -> list[WorkerLease]:
        return list(
            self._session.scalars(
                select(WorkerLease).where(WorkerLease.workspace_id == workspace_id)
            ).all()
        )


class WorkerLifecycleQueueReader:
    def __init__(
        self,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._redis = redis
        self._keys = key_builder

    def queued_jobs(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        limit: int = 1_000,
    ) -> list[JobPayload]:
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return [job for job in queue.peek(limit=limit) if job.workspace_id == workspace_id]
