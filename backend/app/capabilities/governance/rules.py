from __future__ import annotations

from uuid import UUID

GOVERNANCE_APPLY_ACTIONS = {
    "allow_or_remove_configured_mcp_tools",
    "allow_mcp_tools",
    "disable_unusable_skill_installs",
    "disable_blocked_mcp_servers",
    "refresh_mcp_health_check",
    "refresh_mcp_health_checks",
}
DEFAULT_GOVERNANCE_APPLY_ACTIONS = [
    "disable_unusable_skill_installs",
    "disable_blocked_mcp_servers",
]
SKILL_INSTALL_GOVERNANCE_DISABLE_REASONS = {
    "missing_required_mcp_tools",
}
MCP_SERVER_GOVERNANCE_DISABLE_REASONS = {
    "health_check_stale",
    "missing_remote_url",
    "missing_stdio_command",
    "server_unhealthy",
    "unsupported_hosted_transport",
    "unsupported_server_type",
}


def dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def skill_governance_actions(blocked_reasons: list[str]) -> list[str]:
    actions: list[str] = []
    reasons = set(blocked_reasons)
    if "skill_install_disabled" in reasons:
        actions.append("review_skill_install_status")
    if "missing_required_mcp_tools" in reasons:
        actions.append("repair_required_mcp_tools")
    return actions


def agent_governance_actions(blocked_reasons: list[str]) -> list[str]:
    actions: list[str] = []
    reasons = set(blocked_reasons)
    if "agent_inactive" in reasons:
        actions.append("review_agent_status")
    if "missing_agent_skill_installs" in reasons:
        actions.append("repair_agent_skill_assignments")
    if "configured_mcp_tools_not_allowed" in reasons:
        actions.append("allow_or_remove_configured_mcp_tools")
    if "unusable_installed_skills" in reasons:
        actions.append("repair_installed_skill_dependencies")
    if "unavailable_allowed_mcp_tools" in reasons:
        actions.append("repair_mcp_tool_availability")
    return actions


def mcp_server_governance_actions(
    blocked_reasons: list[str],
    *,
    failed_call_count: int,
) -> list[str]:
    actions: list[str] = []
    reasons = set(blocked_reasons)
    if "server_inactive" in reasons:
        actions.append("review_mcp_server_status")
    if "server_unhealthy" in reasons or "health_check_stale" in reasons:
        actions.append("refresh_mcp_health_check")
    if "no_allowed_tools" in reasons:
        actions.append("allow_mcp_tools")
    if "missing_required_credentials" in reasons:
        actions.append("add_mcp_credentials")
    if "unsupported_server_type" in reasons:
        actions.append("replace_unsupported_mcp_server")
    if "missing_stdio_command" in reasons:
        actions.append("configure_stdio_command")
    if "missing_remote_url" in reasons or "unsupported_hosted_transport" in reasons:
        actions.append("repair_mcp_connection")
    if failed_call_count > 0:
        actions.append("inspect_failed_mcp_calls")
    return actions


def governance_actions(actions: list[str] | None) -> list[str]:
    if not actions:
        return list(DEFAULT_GOVERNANCE_APPLY_ACTIONS)
    normalized: list[str] = []
    seen: set[str] = set()
    for action in actions:
        if not isinstance(action, str):
            continue
        item = action.strip()
        if not item or item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized or list(DEFAULT_GOVERNANCE_APPLY_ACTIONS)


def skill_install_should_be_disabled(blocked_reasons: list[str]) -> bool:
    return bool(set(blocked_reasons) & SKILL_INSTALL_GOVERNANCE_DISABLE_REASONS)


def mcp_server_should_be_governance_disabled(blocked_reasons: list[str]) -> bool:
    return bool(set(blocked_reasons) & MCP_SERVER_GOVERNANCE_DISABLE_REASONS)


def mcp_server_should_refresh_health(blocked_reasons: list[str]) -> bool:
    return bool({"health_check_stale", "server_unhealthy"} & set(blocked_reasons))


def mcp_health_refresh_error(blocked_reasons: list[str]) -> str:
    reasons = set(blocked_reasons)
    if "missing_remote_url" in reasons:
        return "missing_remote_url"
    if "missing_stdio_command" in reasons:
        return "missing_stdio_command"
    if "unsupported_hosted_transport" in reasons:
        return "unsupported_hosted_transport"
    if "unsupported_server_type" in reasons:
        return "unsupported_server_type"
    return "mcp_server_not_probeable"


def governance_result(
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    resource_name: str,
    status: str,
    blocked_reasons: list[str],
) -> dict[str, object]:
    return {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "status": status,
        "blocked_reasons": blocked_reasons,
    }


def governance_skipped(
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    resource_name: str,
    reason: str,
    blocked_reasons: list[str],
) -> dict[str, object]:
    return {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "status": "skipped",
        "reason": reason,
        "blocked_reasons": blocked_reasons,
    }
