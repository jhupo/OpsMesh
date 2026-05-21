from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.models import PlatformPolicy

RISKY_EXECUTION_POLICY_KEY = "global_risky_execution"
WORKER_CONTROL_POLICY_KEY = "global_worker_control"


@dataclass(frozen=True)
class RiskyExecutionPolicy:
    allow_runtime_commands: bool = True
    allow_network_egress: bool = False
    allow_self_hosted_runtimes: bool = True
    require_approval_for_high_risk_tools: bool = True
    high_risk_tool_mode: str = "require_workspace_approval"


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


class PlatformPolicyService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def risky_execution_policy(self) -> RiskyExecutionPolicy:
        policy = self._session.scalar(
            select(PlatformPolicy).where(
                PlatformPolicy.policy_key == RISKY_EXECUTION_POLICY_KEY,
                PlatformPolicy.status == "active",
            )
        )
        if policy is None:
            return RiskyExecutionPolicy()
        value = policy.value if isinstance(policy.value, dict) else {}
        return RiskyExecutionPolicy(
            allow_runtime_commands=_bool_value(value, "allow_runtime_commands", True),
            allow_network_egress=_bool_value(value, "allow_network_egress", False),
            allow_self_hosted_runtimes=_bool_value(value, "allow_self_hosted_runtimes", True),
            require_approval_for_high_risk_tools=_bool_value(
                value,
                "require_approval_for_high_risk_tools",
                True,
            ),
            high_risk_tool_mode=_high_risk_tool_mode(value),
        )

    def worker_control_policy(self) -> WorkerControlPolicy:
        policy = self._session.scalar(
            select(PlatformPolicy).where(
                PlatformPolicy.policy_key == WORKER_CONTROL_POLICY_KEY,
                PlatformPolicy.status == "active",
            )
        )
        value = policy.value if policy is not None and isinstance(policy.value, dict) else {}
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


def default_risky_execution_policy_value() -> dict[str, object]:
    policy = RiskyExecutionPolicy()
    return {
        "allow_runtime_commands": policy.allow_runtime_commands,
        "allow_network_egress": policy.allow_network_egress,
        "allow_self_hosted_runtimes": policy.allow_self_hosted_runtimes,
        "require_approval_for_high_risk_tools": policy.require_approval_for_high_risk_tools,
        "high_risk_tool_mode": policy.high_risk_tool_mode,
    }


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


def normalize_risky_execution_policy_value(
    current_value: dict[str, object] | None,
    update: dict[str, object],
) -> dict[str, object]:
    merged = default_risky_execution_policy_value()
    if current_value is not None:
        for key in merged:
            raw_value = current_value.get(key)
            if isinstance(raw_value, bool):
                merged[key] = raw_value
    for key in merged:
        raw_value = update.get(key)
        if isinstance(raw_value, bool):
            merged[key] = raw_value
    raw_mode = update.get("high_risk_tool_mode")
    if isinstance(raw_mode, str) and raw_mode in _HIGH_RISK_TOOL_MODES:
        merged["high_risk_tool_mode"] = raw_mode
        merged["require_approval_for_high_risk_tools"] = raw_mode == "require_workspace_approval"
    elif "require_approval_for_high_risk_tools" in update:
        raw_approval = update.get("require_approval_for_high_risk_tools")
        if isinstance(raw_approval, bool):
            merged["high_risk_tool_mode"] = (
                "require_workspace_approval" if raw_approval else "allow"
            )
    return merged


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


_HIGH_RISK_TOOL_MODES = {"require_workspace_approval", "allow", "block"}


def _high_risk_tool_mode(value: dict[str, object]) -> str:
    raw_mode = value.get("high_risk_tool_mode")
    if isinstance(raw_mode, str) and raw_mode in _HIGH_RISK_TOOL_MODES:
        return raw_mode
    return (
        "require_workspace_approval"
        if _bool_value(value, "require_approval_for_high_risk_tools", True)
        else "allow"
    )
