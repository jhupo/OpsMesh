from __future__ import annotations

from backend.app.runtime.instances.models import WorkspaceRuntime


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
