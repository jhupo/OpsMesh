"""Agent plugin tools, Skills, middleware and lifecycle adapters."""

from collections.abc import Iterable

from opsmesh_sdk import PluginContext

from .registry import InstalledTool, PluginRegistry


def agent_plugin_registry(
    contexts: Iterable[PluginContext],
    *,
    prepared_tools: dict[tuple[str, str], InstalledTool] | None = None,
) -> PluginRegistry:
    registry = PluginRegistry()
    for context in contexts:
        if (
            context.tools
            or context.skills_directory
            or context.middlewares
            or context.hooks
            or context.mcps
        ):
            registry.register(context, prepared_tools=prepared_tools)
    return registry
