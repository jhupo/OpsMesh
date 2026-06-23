from __future__ import annotations

from backend.app.operations.models import WorkerNode


def normalized_worker_capacity(capacity: dict[str, object], worker_type: str) -> dict[str, object]:
    normalized = dict(capacity)
    normalized.setdefault("worker_type", worker_type)
    return normalized


def worker_node_snapshot(node: WorkerNode) -> dict[str, object]:
    return {
        "worker_id": node.worker_id,
        "worker_type": node.worker_type,
        "status": node.status,
        "queue_name": node.queue_name,
        "worker_version": node.worker_version,
        "hostname": node.hostname,
        "capacity": dict(node.capacity),
        "details": dict(node.details),
        "drain_requested_at": node.drain_requested_at.isoformat()
        if node.drain_requested_at is not None
        else None,
    }


def positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default


def first_exceeded_capacity_cap(
    capacity: dict[str, object],
    caps: dict[str, int],
) -> tuple[str, int, int] | None:
    for key, cap in caps.items():
        value = capacity.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        if value > cap:
            return key, value, cap
    return None


def top_counts(counts: dict[str, int], limit: int) -> list[dict[str, object]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
