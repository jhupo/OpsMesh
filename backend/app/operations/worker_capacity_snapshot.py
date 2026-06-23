from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.operations.utils import positive_int
from backend.app.operations.worker_capacity import worker_status_blocks_claims
from backend.app.operations.worker_lease_queries import WorkerLeaseQueryService
from backend.app.operations.worker_models import WorkerCapacitySnapshot
from backend.app.operations.worker_node_repository import WorkerNodeRepository


class WorkerCapacitySnapshotService:
    def __init__(self, session: Session) -> None:
        self._leases = WorkerLeaseQueryService(session)
        self._nodes = WorkerNodeRepository(session)

    def worker_capacity_snapshot(
        self,
        worker_id: str,
        *,
        default_max_jobs: int = 1,
    ) -> WorkerCapacitySnapshot:
        node = self._nodes.by_worker_id(worker_id)
        if node is not None and worker_status_blocks_claims(node):
            running_jobs = self._leases.running_leases_for_worker(worker_id)
            return WorkerCapacitySnapshot(
                worker_id=worker_id,
                max_jobs=positive_int(node.capacity.get("max_jobs"), default_max_jobs),
                running_jobs=running_jobs,
                available_slots=0,
                accepting=False,
                reason=f"worker_{node.status}",
                capacity=dict(node.capacity),
            )

        max_jobs = (
            positive_int(node.capacity.get("max_jobs"), default_max_jobs)
            if node is not None
            else max(1, default_max_jobs)
        )
        running_jobs = self._leases.running_leases_for_worker(worker_id)
        available_slots = max(0, max_jobs - running_jobs)
        return WorkerCapacitySnapshot(
            worker_id=worker_id,
            max_jobs=max_jobs,
            running_jobs=running_jobs,
            available_slots=available_slots,
            accepting=available_slots > 0,
            reason=None if available_slots > 0 else "worker_capacity_full",
            capacity=dict(node.capacity) if node is not None else {"max_jobs": max_jobs},
        )
