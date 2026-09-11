from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict
from uuid import UUID

from backend.app.runtime_manager.contracts import (
    RuntimeHardeningPolicy,
    RuntimeLimits,
)
from backend.app.runtimes.models import WorkspaceRuntime


class WorkspaceMountMetadata(TypedDict):
    type: str
    docker_volume: str
    target: str
    mode: str


class RuntimeIsolationMetadata(TypedDict):
    workspace_id: str
    runtime_id: str
    runtime_space_id: str | None
    workspace_mount: WorkspaceMountMetadata
    network: dict[str, object]


def runtime_space_reservation_key(runtime: WorkspaceRuntime) -> str:
    return f"workspace_runtime:{runtime.id}:docker"


def runtime_space_usage_for_runtime(limits: RuntimeLimits) -> dict[str, int]:
    return {
        "docker_runtimes": 1,
        "cpu": _ceil_positive_int(limits.cpu_count),
        "memory_mb": limits.memory_mb,
        "storage_mb": limits.disk_mb,
    }


def runtime_isolation_metadata(
    *,
    workspace_id: UUID,
    runtime_id: UUID,
    runtime_space_id: UUID | None,
    network_disabled: bool,
    network_policy: Mapping[str, object] | None = None,
) -> RuntimeIsolationMetadata:
    volume_name = _runtime_volume_name(workspace_id, runtime_id)
    return {
        "workspace_id": str(workspace_id),
        "runtime_id": str(runtime_id),
        "runtime_space_id": str(runtime_space_id) if runtime_space_id else None,
        "workspace_mount": {
            "type": "volume",
            "docker_volume": volume_name,
            "target": "/workspace",
            "mode": "rw",
        },
        "network": {
            **dict(network_policy or {}),
            "disabled": network_disabled,
            "mode": (
                str((network_policy or {}).get("mode"))
                if isinstance((network_policy or {}).get("mode"), str)
                else "none" if network_disabled else "internet"
            ),
        },
    }


def default_runtime_hardening_policy() -> RuntimeHardeningPolicy:
    return RuntimeHardeningPolicy()


def runtime_hardening_metadata(
    policy: RuntimeHardeningPolicy,
    *,
    isolation_metadata: Mapping[str, object],
) -> dict[str, object]:
    writable_paths: list[dict[str, object]] = []
    workspace_mount = isolation_metadata.get("workspace_mount")
    if isinstance(workspace_mount, dict):
        writable_paths.append(
            {
                "type": workspace_mount.get("type", "volume"),
                "target": workspace_mount.get("target"),
                "mode": workspace_mount.get("mode", "rw"),
            }
        )
    writable_paths.extend(
        {
            "type": "tmpfs",
            "target": tmpfs.target,
            "mode": tmpfs.mode,
            "size_mb": tmpfs.size_mb,
        }
        for tmpfs in policy.tmpfs
    )
    return {
        "cap_drop": list(policy.cap_drop),
        "security_opt": list(policy.security_opt),
        "read_only_rootfs": policy.read_only_rootfs,
        "writable_paths": writable_paths,
        "user": {
            "value": policy.user,
            "policy": policy.user_policy,
            "enforced": policy.user_enforced,
        },
    }


def runtime_labels(runtime: WorkspaceRuntime) -> dict[str, str]:
    labels = {
        "opsmesh.managed": "true",
        "opsmesh.runtime_type": runtime.runtime_type,
        "opsmesh.runtime_provider": runtime.runtime_provider,
    }
    if runtime.runtime_space_id is not None:
        labels["opsmesh.runtime_space_id"] = str(runtime.runtime_space_id)
    policy_resolution = runtime.capabilities.get("policy_resolution")
    if isinstance(policy_resolution, dict):
        team = policy_resolution.get("team")
        if isinstance(team, dict) and isinstance(team.get("id"), str):
            labels["opsmesh.team_id"] = team["id"]
        runtime_space = policy_resolution.get("runtime_space")
        if isinstance(runtime_space, dict) and isinstance(runtime_space.get("scope"), str):
            labels["opsmesh.runtime_space_scope"] = runtime_space["scope"]
    return labels


def _ceil_positive_int(value: float) -> int:
    integer_value = int(value)
    if value > integer_value:
        integer_value += 1
    return max(1, integer_value)


def _runtime_volume_name(workspace_id: UUID, runtime_id: UUID) -> str:
    return f"opsmesh-ws-{workspace_id.hex}-runtime-{runtime_id.hex}"
