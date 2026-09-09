from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_capacity import (
    WorkerCapacityAggregateResponse,
    WorkerTypeCapacityResponse,
)
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.operations.utils import positive_int
from backend.app.operations.worker_lifecycle import RUNNING_LEASE_STATUSES


class OperationsWorkerCapacityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def worker_capacity_aggregate(self) -> WorkerCapacityAggregateResponse:
        nodes = list(self._session.scalars(select(WorkerNode)).all())
        running_jobs = self._running_job_count()
        max_jobs = sum(positive_int(node.capacity.get("max_jobs"), 1) for node in nodes)
        return WorkerCapacityAggregateResponse(
            workers_total=len(nodes),
            workers_online=sum(1 for node in nodes if node.status == "online"),
            workers_draining=sum(1 for node in nodes if node.status == "draining"),
            workers_offline=sum(1 for node in nodes if node.status == "offline"),
            max_jobs=max_jobs,
            running_jobs=running_jobs,
            available_slots=max(0, max_jobs - running_jobs),
            utilization=round(running_jobs / max_jobs, 4) if max_jobs > 0 else 0.0,
        )

    def worker_type_capacity(self) -> list[WorkerTypeCapacityResponse]:
        nodes = self._session.scalars(select(WorkerNode)).all()
        running_by_worker = dict(
            self._session.execute(
                select(WorkerLease.worker_id, func.count())
                .where(WorkerLease.status.in_(RUNNING_LEASE_STATUSES))
                .group_by(WorkerLease.worker_id)
            )
            .tuples()
            .all()
        )
        grouped: dict[str, dict[str, int]] = {}
        for node in nodes:
            bucket = grouped.setdefault(
                node.worker_type,
                {
                    "workers_total": 0,
                    "workers_online": 0,
                    "workers_draining": 0,
                    "max_jobs": 0,
                    "running_jobs": 0,
                    "available_slots": 0,
                },
            )
            max_jobs = positive_int(node.capacity.get("max_jobs"), 1)
            running_jobs = int(running_by_worker.get(node.worker_id, 0))
            bucket["workers_total"] += 1
            bucket["workers_online"] += 1 if node.status == "online" else 0
            bucket["workers_draining"] += 1 if node.status == "draining" else 0
            bucket["max_jobs"] += max_jobs
            bucket["running_jobs"] += running_jobs
            bucket["available_slots"] += max(0, max_jobs - running_jobs)
        return [
            WorkerTypeCapacityResponse(
                worker_type=worker_type,
                utilization=round(values["running_jobs"] / values["max_jobs"], 4)
                if values["max_jobs"] > 0
                else 0.0,
                **values,
            )
            for worker_type, values in sorted(grouped.items())
        ]

    def _running_job_count(self) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(WorkerLease)
                .where(WorkerLease.status.in_(RUNNING_LEASE_STATUSES))
            )
            or 0
        )
