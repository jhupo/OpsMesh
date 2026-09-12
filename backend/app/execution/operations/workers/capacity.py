from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations.capacity import (
    WorkerCapacityAggregateResponse,
    WorkerTypeCapacityResponse,
)
from backend.app.execution.operations.models import WorkerLease, WorkerNode
from backend.app.execution.operations.workers.lease_queries import WorkerLeaseQueryService
from backend.app.execution.operations.workers.lifecycle import RUNNING_LEASE_STATUSES
from backend.app.execution.operations.workers.node_repository import WorkerNodeRepository
from backend.app.execution.workers.jobs import JobType
from backend.app.platform.common.values import int_or_zero, positive_int_or_default


@dataclass(frozen=True)
class WorkerCapacitySnapshot:
    worker_id: str
    max_jobs: int
    running_jobs: int
    available_slots: int
    accepting: bool
    reason: str | None = None
    capacity: dict[str, object] | None = None


def worker_heartbeat_details(details: dict[str, object]) -> dict[str, object]:
    sanitized = dict(details)
    capacity = sanitized.get("capacity")
    if isinstance(capacity, dict):
        sanitized["capacity"] = dict(capacity)
    enqueued = int_or_zero(sanitized.get("scheduled_job_actions_enqueued"))
    recorded = int_or_zero(sanitized.get("scheduled_job_actions_recorded"))
    skipped = int_or_zero(sanitized.get("scheduled_job_actions_skipped"))
    enqueued_by_type = string_int_dict(sanitized.get("scheduled_job_actions_enqueued_by_job_type"))
    recorded_by_type = string_int_dict(sanitized.get("scheduled_job_actions_recorded_by_job_type"))
    skipped_by_type = string_int_dict(sanitized.get("scheduled_job_actions_skipped_by_job_type"))
    if not any((enqueued, recorded, skipped, enqueued_by_type, recorded_by_type, skipped_by_type)):
        return sanitized
    provider_health_job_type = JobType.MODEL_PROVIDER_HEALTH_CHECK.value
    sanitized["scheduled_job_actions"] = {
        "enqueued": enqueued,
        "recorded": recorded,
        "skipped": skipped,
        "enqueued_by_job_type": enqueued_by_type,
        "recorded_by_job_type": recorded_by_type,
        "skipped_by_job_type": skipped_by_type,
        "model_provider_health_check": {
            "enqueued": enqueued_by_type.get(provider_health_job_type, 0),
            "recorded": recorded_by_type.get(provider_health_job_type, 0),
            "skipped": skipped_by_type.get(provider_health_job_type, 0),
        },
    }
    return sanitized


def string_int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def worker_capacity(capacity: dict[str, object] | None, worker_type: str) -> dict[str, object]:
    normalized = dict(capacity or {})
    normalized.setdefault("worker_type", worker_type)
    return normalized


def merge_worker_capacity(
    current: dict[str, object] | None,
    incoming: dict[str, object] | None,
) -> dict[str, object]:
    merged = dict(current or {})
    merged.update(dict(incoming or {}))
    return merged


def next_worker_node_status(node: WorkerNode, heartbeat_status: str) -> str:
    if node.drain_requested_at is not None:
        return "draining"
    if node.status in {"offline", "maintenance", "disabled", "quarantined"}:
        return node.status
    return heartbeat_status


def worker_status_blocks_claims(node: WorkerNode) -> bool:
    return node.drain_requested_at is not None or node.status in {
        "offline",
        "maintenance",
        "disabled",
        "quarantined",
    }


def bounded_worker_capacity(
    capacity: dict[str, object] | None,
    worker_type: str,
    caps: dict[str, int],
) -> dict[str, object]:
    normalized = worker_capacity(capacity, worker_type)
    for key, cap in caps.items():
        value = normalized.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > cap:
            normalized[key] = cap
    return normalized






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
                max_jobs=positive_int_or_default(node.capacity.get("max_jobs"), default_max_jobs),
                running_jobs=running_jobs,
                available_slots=0,
                accepting=False,
                reason=f"worker_{node.status}",
                capacity=dict(node.capacity),
            )

        max_jobs = (
            positive_int_or_default(node.capacity.get("max_jobs"), default_max_jobs)
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






class OperationsWorkerCapacityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def worker_capacity_aggregate(self) -> WorkerCapacityAggregateResponse:
        nodes = list(self._session.scalars(select(WorkerNode)).all())
        running_jobs = self._running_job_count()
        max_jobs = sum(positive_int_or_default(node.capacity.get("max_jobs"), 1) for node in nodes)
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
            max_jobs = positive_int_or_default(node.capacity.get("max_jobs"), 1)
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
