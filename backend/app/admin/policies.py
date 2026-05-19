from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.models import PlatformPolicy

RISKY_EXECUTION_POLICY_KEY = "global_risky_execution"


@dataclass(frozen=True)
class RiskyExecutionPolicy:
    allow_runtime_commands: bool = True
    allow_network_egress: bool = False
    allow_self_hosted_runtimes: bool = True
    require_approval_for_high_risk_tools: bool = True
    high_risk_tool_mode: str = "require_workspace_approval"


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


def default_risky_execution_policy_value() -> dict[str, object]:
    policy = RiskyExecutionPolicy()
    return {
        "allow_runtime_commands": policy.allow_runtime_commands,
        "allow_network_egress": policy.allow_network_egress,
        "allow_self_hosted_runtimes": policy.allow_self_hosted_runtimes,
        "require_approval_for_high_risk_tools": policy.require_approval_for_high_risk_tools,
        "high_risk_tool_mode": policy.high_risk_tool_mode,
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


def _bool_value(value: dict[str, object], key: str, default: bool) -> bool:
    raw_value = value.get(key)
    return raw_value if isinstance(raw_value, bool) else default


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
