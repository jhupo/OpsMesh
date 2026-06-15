from backend.app.agents.models import AgentProfile


def agent_allowed_mcp_tool_names(agent: AgentProfile) -> set[str] | None:
    configured = agent.tool_policy.get("mcp_tools")
    if configured in (None, "*"):
        return None
    if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        return set(configured)
    return set()


def agent_mcp_policy_mode(agent: AgentProfile) -> tuple[str, list[str] | None]:
    configured = agent.tool_policy.get("mcp_tools")
    if configured in (None, "*"):
        return "all_workspace_tools", None
    if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        return "allowlist", sorted(set(configured))
    return "deny_all", []
