"""Snapshot and validate the complete Agent graph before creating external resources."""

from collections.abc import Callable

from opsmesh.agents.config.agent import AgentConfig
from opsmesh.agents.config.sandbox import SandboxAgentConfig
from opsmesh.agents.config.tools import AgentToolRef


def resolve_agents(
    agent_id: str,
    load: Callable[[str], AgentConfig | SandboxAgentConfig],
    *,
    max_depth: int = 32,
    max_agents: int = 256,
) -> dict[str, AgentConfig | SandboxAgentConfig]:
    if max_depth < 1 or max_agents < 1:
        raise ValueError("Graph limits must be positive")
    resolved: dict[str, AgentConfig | SandboxAgentConfig] = {}
    visiting: set[str] = set()
    heights: dict[str, int] = {}

    def visit(identifier: str, depth: int) -> int:
        if identifier in visiting:
            raise ValueError(f"Agent graph contains a cycle at {identifier}")
        if depth > max_depth:
            raise ValueError("Agent graph exceeds max_depth")
        if identifier in heights:
            if depth + heights[identifier] - 1 > max_depth:
                raise ValueError("Agent graph exceeds max_depth")
            return heights[identifier]
        if len(resolved) >= max_agents:
            raise ValueError("Agent graph exceeds max_agents")
        config = load(identifier).model_copy(deep=True)
        resolved[identifier] = config
        visiting.add(identifier)
        children = [tool.agent_id for tool in config.tools if isinstance(tool, AgentToolRef)]
        children.extend(handoff.agent_id for handoff in config.handoffs)
        height = 1
        for child in children:
            height = max(height, visit(child, depth + 1) + 1)
        visiting.remove(identifier)
        heights[identifier] = height
        return height

    visit(agent_id, 1)
    return resolved
