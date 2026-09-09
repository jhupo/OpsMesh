from __future__ import annotations

from dataclasses import dataclass, replace
from typing import NotRequired, TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceBinding
from backend.app.runtimes.models import RuntimeTemplate
from backend.app.teams.models import AgentTeam


@dataclass(frozen=True)
class RuntimePolicyResolution:
    limits: RuntimeLimits
    network_disabled: bool
    metadata: dict[str, object]


class RuntimePolicyMetadata(TypedDict):
    template_id: str
    limits_source: str
    requested: dict[str, object]
    effective: dict[str, object]
    sources: list[str]
    limit_reductions: list[dict[str, object]]
    runtime_space: NotRequired[dict[str, object]]
    team: NotRequired[dict[str, object]]


class RuntimePolicyResolver:
    def __init__(self, session: Session) -> None:
        self._session = session

    def limits_from_template(self, template: RuntimeTemplate) -> RuntimeLimits:
        default_limits = template.default_limits or {}
        return RuntimeLimits(
            cpu_count=as_float(default_limits.get("cpu_count"), 1),
            memory_mb=as_int(default_limits.get("memory_mb"), 512),
            disk_mb=as_int(default_limits.get("disk_mb"), 1024),
            timeout_seconds=as_int(default_limits.get("timeout_seconds"), 60),
            max_output_bytes=as_int(default_limits.get("max_output_bytes"), 256_000),
            max_processes=as_int(default_limits.get("max_processes"), 256),
        )

    def resolve_runtime_policy(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID | None,
        template: RuntimeTemplate,
        requested_limits: RuntimeLimits | None,
        requested_network_disabled: bool,
    ) -> RuntimePolicyResolution:
        limits = requested_limits or self.limits_from_template(template)
        network_disabled = requested_network_disabled
        metadata: RuntimePolicyMetadata = {
            "template_id": str(template.id),
            "limits_source": "request" if requested_limits is not None else "template",
            "requested": {
                "network_disabled": requested_network_disabled,
                "limits": limits_metadata(limits),
            },
            "effective": {},
            "sources": [],
            "limit_reductions": [],
        }
        if runtime_space_id is not None:
            runtime_space = self._session.scalar(
                select(RuntimeSpace).where(
                    RuntimeSpace.workspace_id == workspace_id,
                    RuntimeSpace.id == runtime_space_id,
                )
            )
            if runtime_space is not None:
                limits, network_disabled = self._apply_runtime_space_policy(
                    workspace_id=workspace_id,
                    runtime_space=runtime_space,
                    metadata=metadata,
                    limits=limits,
                    network_disabled=network_disabled,
                )
        metadata["effective"] = {
            "network_disabled": network_disabled,
            "limits": limits_metadata(limits),
        }
        return RuntimePolicyResolution(
            limits=limits,
            network_disabled=network_disabled,
            metadata=dict(metadata),
        )

    def _apply_runtime_space_policy(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        metadata: RuntimePolicyMetadata,
        limits: RuntimeLimits,
        network_disabled: bool,
    ) -> tuple[RuntimeLimits, bool]:
        runtime_space_policy = runtime_space_policy_payload(runtime_space)
        metadata["runtime_space"] = {
            "id": str(runtime_space.id),
            "scope": runtime_space.scope,
            "status": runtime_space.status,
        }
        metadata["sources"].append("runtime_space")
        network_disabled = network_disabled or policy_disables_network(runtime_space_policy)
        limits, reductions = apply_limit_caps(
            limits,
            runtime_space_policy,
            source="runtime_space",
        )
        metadata["limit_reductions"].extend(reductions)

        team = self._team_for_runtime_space(workspace_id, runtime_space)
        if team is not None:
            team_policy = team_runtime_policy(team)
            metadata["team"] = {
                "id": str(team.id),
                "name": team.name,
                "team_type": team.team_type,
            }
            metadata["sources"].append("team")
            network_disabled = network_disabled or policy_disables_network(team_policy)
            limits, reductions = apply_limit_caps(limits, team_policy, source="team")
            metadata["limit_reductions"].extend(reductions)
        return limits, network_disabled

    def _team_for_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
    ) -> AgentTeam | None:
        if runtime_space.scope != "team":
            return None
        binding = self._session.scalar(
            select(RuntimeSpaceBinding).where(
                RuntimeSpaceBinding.workspace_id == workspace_id,
                RuntimeSpaceBinding.runtime_space_id == runtime_space.id,
                RuntimeSpaceBinding.target_type == "agent_team",
                RuntimeSpaceBinding.status == "active",
            )
        )
        if binding is None:
            return None
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == binding.target_id,
                AgentTeam.status == "active",
            )
        )


def as_float(value: object, fallback: float) -> float:
    if isinstance(value, int | float | str):
        return float(value)
    return fallback


def as_int(value: object, fallback: int) -> int:
    if isinstance(value, int | float | str):
        return int(value)
    return fallback


def runtime_space_policy_payload(runtime_space: RuntimeSpace) -> dict[str, object]:
    policy = dict_value(runtime_space.policy)
    runtime_policy = dict_value(policy.get("runtime"))
    network_policy = dict_value(runtime_space.network_policy)
    storage_policy = dict_value(runtime_space.storage_policy)
    merged = dict(runtime_policy)
    if network_policy:
        merged.setdefault("network", network_policy)
    if storage_policy:
        merged.setdefault("storage", storage_policy)
    return merged


def team_runtime_policy(team: AgentTeam) -> dict[str, object]:
    default_policy = dict_value(team.default_task_policy)
    return dict_value(default_policy.get("runtime"))


def policy_disables_network(policy: dict[str, object]) -> bool:
    network = policy.get("network")
    if isinstance(network, dict):
        if network.get("disabled") is True:
            return True
        mode = network.get("mode")
        return isinstance(mode, str) and mode.lower() in {"none", "disabled", "off"}
    return False


def apply_limit_caps(
    limits: RuntimeLimits,
    policy: dict[str, object],
    *,
    source: str,
) -> tuple[RuntimeLimits, list[dict[str, object]]]:
    caps = limit_caps(policy)
    if not caps:
        return limits, []
    values: dict[str, int | float] = {
        "cpu_count": limits.cpu_count,
        "memory_mb": limits.memory_mb,
        "disk_mb": limits.disk_mb,
        "timeout_seconds": limits.timeout_seconds,
        "max_output_bytes": limits.max_output_bytes,
        "max_processes": limits.max_processes,
    }
    reductions: list[dict[str, object]] = []
    for key, cap in caps.items():
        current = values[key]
        if isinstance(current, int | float) and current > cap:
            values[key] = cap
            reductions.append(
                {
                    "source": source,
                    "limit": key,
                    "requested": current,
                    "effective": cap,
                }
            )
    return (
        replace(
            limits,
            cpu_count=float(values["cpu_count"]),
            memory_mb=int(values["memory_mb"]),
            disk_mb=int(values["disk_mb"]),
            timeout_seconds=int(values["timeout_seconds"]),
            max_output_bytes=int(values["max_output_bytes"]),
            max_processes=int(values["max_processes"]),
        ),
        reductions,
    )


def limit_caps(policy: dict[str, object]) -> dict[str, int | float]:
    source = dict(dict_value(policy.get("limits")))
    caps: dict[str, int | float] = {}
    for target in (
        "cpu_count",
        "memory_mb",
        "disk_mb",
        "timeout_seconds",
        "max_output_bytes",
        "max_processes",
    ):
        value = positive_number(source.get(target))
        if value is not None:
            caps[target] = value
    return caps


def positive_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        if parsed > 0:
            return parsed
    return None


def limits_metadata(limits: RuntimeLimits) -> dict[str, object]:
    return {
        "cpu_count": limits.cpu_count,
        "memory_mb": limits.memory_mb,
        "disk_mb": limits.disk_mb,
        "timeout_seconds": limits.timeout_seconds,
        "max_output_bytes": limits.max_output_bytes,
        "max_processes": limits.max_processes,
    }


def dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
