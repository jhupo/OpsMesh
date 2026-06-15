from __future__ import annotations

from backend.app.core.typing import dict_or_empty
from backend.app.workers.jobs import JobPayload


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
