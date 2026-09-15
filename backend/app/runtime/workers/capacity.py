from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.utils import dict_or_empty, positive_int_or_default
from backend.app.runtime.workers.contracts import JobPayload
from backend.app.runtime.workers.leases import WorkerLeaseQueryService
from backend.app.runtime.workers.models import WorkerNode


@dataclass(frozen=True)
class WorkerCapacitySnapshot:
    worker_id: str
    max_jobs: int
    running_jobs: int
    available_slots: int
    accepting: bool
    reason: str | None = None
    capacity: dict[str, object] | None = None


class WorkerCapacitySnapshotService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._leases = WorkerLeaseQueryService(session)

    def worker_capacity_snapshot(
        self,
        worker_id: str,
        *,
        default_max_jobs: int = 1,
    ) -> WorkerCapacitySnapshot:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
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


def worker_can_run_job(job: JobPayload, capacity: dict[str, object]) -> bool:
    routing = job.routing
    if not routing:
        return True
    required_worker_types = string_set(routing.get("worker_types"))
    worker_type = capacity_string(capacity, "worker_type")
    if required_worker_types and worker_type not in required_worker_types:
        return False
    required_capabilities = string_set(routing.get("capabilities"))
    worker_capabilities = string_set(capacity.get("capabilities"))
    if required_capabilities and not required_capabilities <= worker_capabilities:
        return False
    required_runtime_modes = string_set(routing.get("runtime_modes"))
    worker_runtime_modes = string_set(capacity.get("runtime_modes"))
    if required_runtime_modes and not required_runtime_modes <= worker_runtime_modes:
        return False
    resource_requirements = dict_or_empty(routing.get("resource_requirements"))
    for key, required_value in resource_requirements.items():
        if positive_number(capacity.get(key)) < positive_number(required_value):
            return False
    return True


def merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        if value <= 0:
            continue
        target[key] = target.get(key, 0) + value


def capacity_string(capacity: dict[str, object], key: str) -> str:
    value = capacity.get(key)
    return value if isinstance(value, str) else ""


def positive_number(value: object) -> float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float) and value > 0:
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return 0
        return parsed if parsed > 0 else 0
    return 0


def string_set(value: object) -> set[str]:
    if isinstance(value, str) and value:
        return {value}
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}
