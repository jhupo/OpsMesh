from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.runtime.operations.contracts.capacity import OperationsWorkerLifecycleResponse
from backend.app.runtime.operations.workers.lifecycle_buckets import (
    WorkerLifecycleBucketAccumulator,
)
from backend.app.runtime.workers.contracts import JobPayload
from backend.app.runtime.workers.models import WorkerLease, WorkerNode
from backend.app.runtime.workers.queue import RedisQueue


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
