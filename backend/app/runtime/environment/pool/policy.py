from __future__ import annotations

from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.runtime.contracts import RuntimeEnvironmentError
from backend.app.runtime.environment.models import WorkspaceRuntime
from backend.app.runtime.environment.project_files import RUNTIME_WORKSPACE_ROOT


def runtime_pool_key(runtime: WorkspaceRuntime) -> str:
    return runtime.pool_key or f"runtime:{runtime.id}"


def pool_policy_matches(parent: WorkspaceRuntime, member: WorkspaceRuntime) -> bool:
    return (
        parent.runtime_provider == member.runtime_provider
        and parent.runtime_type == member.runtime_type
        and parent.runtime_template_id == member.runtime_template_id
        and parent.runtime_space_id == member.runtime_space_id
        and dict(parent.limits or {}) == dict(member.limits or {})
        and dict(parent.network_policy or {}) == dict(member.network_policy or {})
        and isolation_policy(parent) == isolation_policy(member)
    )


def isolation_policy(runtime: WorkspaceRuntime) -> dict[str, object]:
    capabilities = runtime.capabilities or {}
    isolation = capabilities.get("isolation")
    mount_policy: dict[str, object] = {}
    network_policy: dict[str, object] = {}
    if isinstance(isolation, dict):
        mount = isolation.get("workspace_mount")
        if isinstance(mount, dict):
            mount_policy = {
                "type": mount.get("type"),
                "target": mount.get("target"),
                "mode": mount.get("mode"),
            }
        network = isolation.get("network")
        if isinstance(network, dict):
            network_policy = dict(network)
    hardening = capabilities.get("hardening")
    return {
        "isolation": {
            "workspace_mount": mount_policy,
            "network": network_policy,
        },
        "hardening": dict(hardening) if isinstance(hardening, dict) else {},
    }


def pooled_isolation_metadata(
    parent: WorkspaceRuntime,
    member: WorkspaceRuntime,
    run: AgentRun,
) -> dict[str, object]:
    isolation = member.capabilities.get("isolation")
    if not isinstance(isolation, dict):
        raise RuntimeEnvironmentError(
            "runtime_isolation_unverified",
            "Pooled runtime member has no platform isolation evidence",
        )
    workspace_mount = isolation.get("workspace_mount")
    if not isinstance(workspace_mount, dict):
        raise RuntimeEnvironmentError(
            "runtime_isolation_unverified",
            "Pooled runtime member has no workspace mount evidence",
        )
    return {
        "workspace_id": str(run.workspace_id),
        "runtime_id": str(run.id),
        "runtime_space_id": str(member.runtime_space_id) if member.runtime_space_id else None,
        "workspace_mount": dict(workspace_mount),
        "network": dict(isolation.get("network") or {}),
        "execution": {
            "mode": "pooled",
            "parent_runtime_id": str(parent.id),
            "pool_member_runtime_id": str(member.id),
            "run_id": str(run.id),
            "workspace_root": f"{RUNTIME_WORKSPACE_ROOT}/runs/{run.id}",
        },
    }


def runtime_timeout(limits: dict[str, object]) -> int:
    value = limits.get("timeout_seconds")
    if isinstance(value, int) and value > 0:
        return value
    raise RuntimeEnvironmentError(
        "runtime_limits_invalid",
        "Pooled runtime timeout limit is invalid",
    )
