from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.core.config import Settings
from backend.app.runtime_manager.contracts import DockerRuntimeClient, RuntimeLimits
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.safety import RuntimeSafetyPolicy
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceBinding
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from backend.app.teams.models import AgentTeam


@dataclass(frozen=True)
class RuntimePolicyResolution:
    limits: RuntimeLimits
    network_disabled: bool
    metadata: dict[str, object]


class RuntimeControlService:
    def __init__(
        self,
        session: Session,
        docker_client: DockerRuntimeClient,
        settings: Settings,
    ) -> None:
        self._session = session
        self._manager = RuntimeManager(
            session,
            docker_client,
            managed_host_roots=[Path(settings.storage_root).resolve() / "runtimes"],
        )
        self._safety = RuntimeSafetyPolicy(
            tuple(settings.runtime_allowed_images),
            PlatformPolicyService(session).risky_execution_policy(),
        )

    def list_templates(self) -> list[RuntimeTemplate]:
        return list(
            self._session.scalars(
                select(RuntimeTemplate)
                .where(RuntimeTemplate.status == "active")
                .order_by(RuntimeTemplate.created_at.desc(), RuntimeTemplate.name)
            )
        )

    def create_runtime(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        name: str,
        limits: RuntimeLimits | None,
        network_disabled: bool,
        runtime_space_id: UUID | None = None,
    ) -> WorkspaceRuntime | None:
        template = self._session.get(RuntimeTemplate, template_id)
        if template is None:
            return None
        if runtime_space_id is not None:
            RuntimeSpaceService(self._session).require_runtime_space(
                workspace_id,
                runtime_space_id,
            )
        policy = self._resolve_runtime_policy(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            template=template,
            requested_limits=limits,
            requested_network_disabled=network_disabled,
        )
        self._safety.assert_template_allowed(template)
        self._safety.assert_network_allowed(
            template,
            network_disabled=policy.network_disabled,
        )
        return self._manager.create_runtime(
            workspace_id=workspace_id,
            template=template,
            name=name,
            limits=policy.limits,
            runtime_space_id=runtime_space_id,
            network_disabled=policy.network_disabled,
            policy_metadata=policy.metadata,
        )

    def list_runtimes(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        query: Select[tuple[WorkspaceRuntime]] = select(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == workspace_id,
            WorkspaceRuntime.status != "deleted",
        )
        count_query = select(func.count()).select_from(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == workspace_id,
            WorkspaceRuntime.status != "deleted",
        )
        if status is not None:
            query = query.where(WorkspaceRuntime.status == status)
            count_query = count_query.where(WorkspaceRuntime.status == status)
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                query.order_by(WorkspaceRuntime.created_at.desc()).limit(limit).offset(offset)
            )
        )
        return items, total

    def get_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.id == runtime_id,
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status != "deleted",
            )
        )

    def start_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._manager.start_runtime(runtime)

    def stop_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._manager.stop_runtime(runtime)

    def delete_runtime(self, workspace_id: UUID, runtime_id: UUID) -> bool:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return False
        self._manager.delete_runtime(runtime)
        return True

    def execute_command(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        command: list[str],
    ) -> RuntimeCommand | None:
        runtime = self.get_runtime(workspace_id, runtime_id)
        if runtime is None:
            return None
        return self._manager.execute_command(
            workspace_id=workspace_id,
            runtime=runtime,
            command=command,
        )

    def list_commands(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[RuntimeCommand], int] | None:
        if self.get_runtime(workspace_id, runtime_id) is None:
            return None
        count_query = select(func.count()).select_from(RuntimeCommand).where(
            RuntimeCommand.workspace_id == workspace_id,
            RuntimeCommand.workspace_runtime_id == runtime_id,
        )
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                select(RuntimeCommand)
                .where(
                    RuntimeCommand.workspace_id == workspace_id,
                    RuntimeCommand.workspace_runtime_id == runtime_id,
                )
                .order_by(RuntimeCommand.started_at.desc().nullslast(), RuntimeCommand.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def list_events(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[RuntimeEvent], int] | None:
        if self.get_runtime(workspace_id, runtime_id) is None:
            return None
        count_query = select(func.count()).select_from(RuntimeEvent).where(
            RuntimeEvent.workspace_id == workspace_id,
            RuntimeEvent.workspace_runtime_id == runtime_id,
        )
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                select(RuntimeEvent)
                .where(
                    RuntimeEvent.workspace_id == workspace_id,
                    RuntimeEvent.workspace_runtime_id == runtime_id,
                )
                .order_by(RuntimeEvent.created_at.desc(), RuntimeEvent.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def _limits_from_template(self, template: RuntimeTemplate) -> RuntimeLimits:
        default_limits = template.default_limits or {}
        return RuntimeLimits(
            cpu_count=_as_float(default_limits.get("cpu_count"), 1),
            memory_mb=_as_int(default_limits.get("memory_mb"), 512),
            disk_mb=_as_int(default_limits.get("disk_mb"), 1024),
            timeout_seconds=_as_int(default_limits.get("timeout_seconds"), 60),
            max_output_bytes=_as_int(default_limits.get("max_output_bytes"), 256_000),
            max_processes=_as_int(default_limits.get("max_processes"), 256),
        )

    def _resolve_runtime_policy(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID | None,
        template: RuntimeTemplate,
        requested_limits: RuntimeLimits | None,
        requested_network_disabled: bool,
    ) -> RuntimePolicyResolution:
        limits = requested_limits or self._limits_from_template(template)
        network_disabled = requested_network_disabled
        metadata: dict[str, object] = {
            "template_id": str(template.id),
            "limits_source": "request" if requested_limits is not None else "template",
            "requested": {
                "network_disabled": requested_network_disabled,
                "limits": _limits_metadata(limits),
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
                runtime_space_policy = _runtime_space_policy(runtime_space)
                metadata["runtime_space"] = {
                    "id": str(runtime_space.id),
                    "scope": runtime_space.scope,
                    "status": runtime_space.status,
                }
                metadata["sources"].append("runtime_space")
                network_disabled = network_disabled or _policy_disables_network(
                    runtime_space_policy,
                )
                limits, reductions = _apply_limit_caps(
                    limits,
                    runtime_space_policy,
                    source="runtime_space",
                )
                metadata["limit_reductions"].extend(reductions)

                team = self._team_for_runtime_space(workspace_id, runtime_space)
                if team is not None:
                    team_policy = _team_runtime_policy(team)
                    metadata["team"] = {
                        "id": str(team.id),
                        "name": team.name,
                        "team_type": team.team_type,
                    }
                    metadata["sources"].append("team")
                    network_disabled = network_disabled or _policy_disables_network(team_policy)
                    limits, reductions = _apply_limit_caps(
                        limits,
                        team_policy,
                        source="team",
                    )
                    metadata["limit_reductions"].extend(reductions)
        metadata["effective"] = {
            "network_disabled": network_disabled,
            "limits": _limits_metadata(limits),
        }
        return RuntimePolicyResolution(
            limits=limits,
            network_disabled=network_disabled,
            metadata=metadata,
        )

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


def _as_float(value: object, fallback: float) -> float:
    if isinstance(value, int | float | str):
        return float(value)
    return fallback


def _as_int(value: object, fallback: int) -> int:
    if isinstance(value, int | float | str):
        return int(value)
    return fallback


def _runtime_space_policy(runtime_space: RuntimeSpace) -> dict[str, object]:
    policy = _dict_value(runtime_space.policy)
    runtime_policy = _dict_value(policy.get("runtime"))
    network_policy = _dict_value(runtime_space.network_policy)
    storage_policy = _dict_value(runtime_space.storage_policy)
    merged = dict(runtime_policy)
    if network_policy:
        merged.setdefault("network", network_policy)
    if storage_policy:
        merged.setdefault("storage", storage_policy)
    return merged


def _team_runtime_policy(team: AgentTeam) -> dict[str, object]:
    default_policy = _dict_value(team.default_task_policy)
    return _dict_value(default_policy.get("runtime"))


def _policy_disables_network(policy: dict[str, object]) -> bool:
    network = policy.get("network")
    if isinstance(network, dict):
        if network.get("disabled") is True:
            return True
        mode = network.get("mode")
        return isinstance(mode, str) and mode.lower() in {"none", "disabled", "off"}
    return False


def _apply_limit_caps(
    limits: RuntimeLimits,
    policy: dict[str, object],
    *,
    source: str,
) -> tuple[RuntimeLimits, list[dict[str, object]]]:
    caps = _limit_caps(policy)
    if not caps:
        return limits, []
    values: dict[str, object] = {
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


def _limit_caps(policy: dict[str, object]) -> dict[str, int | float]:
    source = dict(_dict_value(policy.get("limits")))
    caps: dict[str, int | float] = {}
    for target in (
        "cpu_count",
        "memory_mb",
        "disk_mb",
        "timeout_seconds",
        "max_output_bytes",
        "max_processes",
    ):
        value = _positive_number(source.get(target))
        if value is not None:
            caps[target] = value
    return caps


def _positive_number(value: object) -> int | float | None:
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


def _limits_metadata(limits: RuntimeLimits) -> dict[str, object]:
    return {
        "cpu_count": limits.cpu_count,
        "memory_mb": limits.memory_mb,
        "disk_mb": limits.disk_mb,
        "timeout_seconds": limits.timeout_seconds,
        "max_output_bytes": limits.max_output_bytes,
        "max_processes": limits.max_processes,
    }


def _dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
