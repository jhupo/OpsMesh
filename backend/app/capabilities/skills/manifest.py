from __future__ import annotations

from uuid import UUID

from backend.app.agents.models import AgentProfile


def manifest_mcp_tools(manifest: dict[str, object]) -> list[str]:
    raw_tools = manifest.get("mcp_tools")
    if raw_tools is None:
        raw_tools = manifest.get("tools")
    if not isinstance(raw_tools, list):
        return []
    tools: list[str] = []
    seen: set[str] = set()
    for item in raw_tools:
        tool_name: str | None = None
        if isinstance(item, str):
            tool_name = item
        elif isinstance(item, dict) and isinstance(item.get("tool_name"), str):
            tool_name = item["tool_name"]
        if tool_name is None or not tool_name.strip() or tool_name in seen:
            continue
        tools.append(tool_name)
        seen.add(tool_name)
    return tools


def agent_installed_skill_ids(agent: AgentProfile) -> list[UUID]:
    raw_ids = agent.skills.get("installed_skill_ids")
    if not isinstance(raw_ids, list):
        return []
    install_ids: list[UUID] = []
    seen: set[UUID] = set()
    for raw_id in raw_ids:
        try:
            install_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        if install_id in seen:
            continue
        install_ids.append(install_id)
        seen.add(install_id)
    return install_ids
