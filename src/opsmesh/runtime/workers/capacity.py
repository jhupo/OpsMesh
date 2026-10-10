from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.runtime.queues.contracts import JobPayload
from opsmesh.runtime.workers.leases import WorkerLeaseQueryService
from opsmesh.runtime.workers.models import WorkerNode
from opsmesh.shared.utils import dict_or_empty, positive_int_or_default


@dataclass(frozen=True)
class WorkerCapacitySnapshot:
    worker_id: str
    max_jobs: int
    running_jobs: int
    available_slots: int
    accepting: bool
    running_jobs_by_type: dict[str, int]
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
            running_jobs_by_type = self._leases.running_lease_counts_for_worker(worker_id)
            return WorkerCapacitySnapshot(
                worker_id=worker_id,
                max_jobs=positive_int_or_default(node.capacity.get("max_jobs"), default_max_jobs),
                running_jobs=running_jobs,
                available_slots=0,
                accepting=False,
                running_jobs_by_type=running_jobs_by_type,
                reason=f"worker_{node.status}",
                capacity=dict(node.capacity),
            )

        max_jobs = (
            positive_int_or_default(node.capacity.get("max_jobs"), default_max_jobs)
            if node is not None
            else max(1, default_max_jobs)
        )
        running_jobs = self._leases.running_leases_for_worker(worker_id)
        running_jobs_by_type = self._leases.running_lease_counts_for_worker(worker_id)
        available_slots = max(0, max_jobs - running_jobs)
        node_capacity: dict[str, object] = (
            dict(node.capacity) if node is not None else {"max_jobs": max_jobs}
        )
        node_capacity["running_jobs"] = running_jobs
        node_capacity["running_jobs_by_type"] = running_jobs_by_type
        return WorkerCapacitySnapshot(
            worker_id=worker_id,
            max_jobs=max_jobs,
            running_jobs=running_jobs,
            available_slots=available_slots,
            accepting=available_slots > 0,
            running_jobs_by_type=running_jobs_by_type,
            reason=None if available_slots > 0 else "worker_capacity_full",
            capacity=node_capacity,
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
    required_regions = string_set(routing.get("regions"))
    worker_region = capacity_string(capacity, "region")
    if required_regions and worker_region not in required_regions:
        return False
    job_class = "mcp" if job.job_type.value == "mcp.process_control" else "task"
    slot_key = "mcp_slots" if job_class == "mcp" else "task_slots"
    running_key = "running_mcp_jobs" if job_class == "mcp" else "running_task_jobs"
    slot_limit = positive_number(capacity.get(slot_key))
    running_by_type = capacity.get("running_jobs_by_type")
    if isinstance(running_by_type, dict):
        running = sum(
            int(value)
            for key, value in running_by_type.items()
            if isinstance(key, str)
            and (key == "mcp.process_control") == (job_class == "mcp")
            and isinstance(value, int)
            and value >= 0
        )
    else:
        running = int(positive_number(capacity.get(running_key)))
    if slot_limit > 0 and running >= slot_limit:
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
