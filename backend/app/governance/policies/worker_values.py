from __future__ import annotations

from dataclasses import dataclass

WORKER_CONTROL_POLICY_KEY = "global_worker_control"


@dataclass(frozen=True)
class WorkerControlPolicy:
    managed_by: str = "platform_admin"
    allow_status_updates: bool = True
    allow_capacity_updates: bool = True
    allow_queue_updates: bool = True
    allowed_statuses: tuple[str, ...] = ("online", "offline", "draining", "maintenance")
    allowed_worker_types: tuple[str, ...] = ("cloud", "self_hosted")
    max_capacity: dict[str, int] | None = None

    def capacity_caps(self) -> dict[str, int]:
        return dict(self.max_capacity or _DEFAULT_WORKER_CAPACITY_CAPS)


def worker_control_policy_from_value(value: dict[str, object]) -> WorkerControlPolicy:
    normalized = normalize_worker_control_policy_value(None, value)
    return WorkerControlPolicy(
        managed_by=_string_value(normalized, "managed_by", "platform_admin"),
        allow_status_updates=_bool_value(normalized, "allow_status_updates", True),
        allow_capacity_updates=_bool_value(normalized, "allow_capacity_updates", True),
        allow_queue_updates=_bool_value(normalized, "allow_queue_updates", True),
        allowed_statuses=tuple(_string_list(normalized.get("allowed_statuses"))),
        allowed_worker_types=tuple(_string_list(normalized.get("allowed_worker_types"))),
        max_capacity=_int_dict(normalized.get("max_capacity")),
    )


def default_worker_control_policy_value() -> dict[str, object]:
    return {
        "managed_by": "platform_admin",
        "allow_status_updates": True,
        "allow_capacity_updates": True,
        "allow_queue_updates": True,
        "allowed_statuses": ["online", "offline", "draining", "maintenance"],
        "allowed_worker_types": ["cloud", "self_hosted"],
        "max_capacity": dict(_DEFAULT_WORKER_CAPACITY_CAPS),
    }


def normalize_worker_control_policy_value(
    current_value: dict[str, object] | None,
    update: dict[str, object],
) -> dict[str, object]:
    merged = default_worker_control_policy_value()
    if current_value is not None:
        _merge_worker_control_policy(merged, current_value)
    _merge_worker_control_policy(merged, update)
    return merged


def _merge_worker_control_policy(
    target: dict[str, object],
    value: dict[str, object],
) -> None:
    raw_managed_by = value.get("managed_by")
    if isinstance(raw_managed_by, str) and raw_managed_by.strip():
        target["managed_by"] = raw_managed_by.strip()
    for key in ("allow_status_updates", "allow_capacity_updates", "allow_queue_updates"):
        raw_value = value.get(key)
        if isinstance(raw_value, bool):
            target[key] = raw_value
    statuses = _string_list(value.get("allowed_statuses"))
    if statuses:
        target["allowed_statuses"] = statuses
    worker_types = _string_list(value.get("allowed_worker_types"))
    if worker_types:
        target["allowed_worker_types"] = worker_types
    capacity_caps = _int_dict(value.get("max_capacity"))
    if capacity_caps:
        target["max_capacity"] = capacity_caps


def _bool_value(value: dict[str, object], key: str, default: bool) -> bool:
    raw_value = value.get(key)
    return raw_value if isinstance(raw_value, bool) else default


def _string_value(value: dict[str, object], key: str, default: str) -> str:
    raw_value = value.get(key)
    return raw_value if isinstance(raw_value, str) and raw_value else default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        normalized.append(stripped)
    return normalized


def _int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): raw_value
        for key, raw_value in value.items()
        if isinstance(raw_value, int) and not isinstance(raw_value, bool) and raw_value > 0
    }


_DEFAULT_WORKER_CAPACITY_CAPS = {
    "max_jobs": 64,
    "cpu_count": 128,
    "memory_mb": 1_048_576,
}
