"""Resolve stable resource aliases only after authorized tool discovery."""

from collections.abc import Sequence
from typing import Any

from opsmesh.framework.agents import AgentSpecification, ToolBinding
from opsmesh.framework.errors import OpsMeshError


def resolve_tool_selectors(
    spec: AgentSpecification, resources: Sequence[ToolBinding]
) -> AgentSpecification:
    names: dict[str, str] = {}
    callable_names = {binding.name for binding in (*spec.tools, *resources)} | {
        tool.name for tool in spec.agent_tools
    }
    for binding in (*spec.tools, *resources):
        for alias in (binding.name, *binding.aliases):
            if alias in names and names[alias] != binding.name:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Prepared tool selectors collide", details={"selector": alias}
                )
            names[alias] = binding.name
    for name in callable_names:
        if name in names and names[name] != name:
            raise OpsMeshError("CONFIG_INVALID", "Tool alias shadows another tool")
        names[name] = name
    choices = dict(names)
    for tool in spec.hosted_tools:
        choices[tool.type] = tool.type
    for native_tool in spec.native_tools:
        choices[native_tool.type] = native_tool.type
    for handoff_binding in spec.handoffs:
        if handoff_binding.tool_name:
            choices[handoff_binding.tool_name] = handoff_binding.tool_name
    selected: dict[str, Any] = {}
    if isinstance(spec.tool_choice, str) and spec.tool_choice not in ("auto", "required", "none"):
        if spec.tool_choice not in choices:
            raise OpsMeshError(
                "TOOL_SELECTION_UNAVAILABLE",
                "Selected tool is not in the authorized prepared tool set",
                details={"selector": spec.tool_choice},
            )
        selected["tool_choice"] = choices[spec.tool_choice]
    stops = []
    for selector in spec.stop_tools:
        if selector not in names or names[selector] not in callable_names:
            raise OpsMeshError(
                "TOOL_SELECTION_UNAVAILABLE",
                "Stop tool is not in the authorized function tool set",
                details={"selector": selector},
            )
        stops.append(names[selector])
    if stops:
        selected["stop_tools"] = tuple(stops)
    return spec.model_copy(update=selected)
