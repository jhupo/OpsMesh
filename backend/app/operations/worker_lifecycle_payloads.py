from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_capacity import OperationsWorkerLifecycleResponse
from backend.app.operations.worker_lifecycle_buckets import WorkerLifecycleBucketAccumulator
from backend.app.operations.worker_lifecycle_queries import WorkerLifecycleQueryService
from backend.app.operations.worker_lifecycle_queue import WorkerLifecycleQueueReader
from backend.app.redis.keys import RedisKeyBuilder


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
