from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.capabilities.catalog.product_tools import PRODUCT_TOOL_NAMES


def agent_allowed_mcp_tool_names(agent: AgentProfile) -> set[str]:
    configured = agent.tool_policy.get("allowed_tools", [])
    if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        return set(configured) - PRODUCT_TOOL_NAMES
    return set()


def agent_mcp_policy_mode(agent: AgentProfile) -> tuple[str, list[str]]:
    configured = agent.tool_policy.get("allowed_tools", [])
    if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        return "allowlist", sorted(set(configured) - PRODUCT_TOOL_NAMES)
    return "deny_all", []
