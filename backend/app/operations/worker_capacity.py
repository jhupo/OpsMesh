from __future__ import annotations

from backend.app.core.typing import int_or_zero
from backend.app.operations.models import WorkerNode
from backend.app.workers.jobs import JobType


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
