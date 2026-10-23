"""Resolve selected plugin MCP factories through caller-owned native connection lifespans."""

import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from typing import Any

from agents.mcp import MCPServer
from opsmesh_sdk import PluginContext
from opsmesh_sdk.registration import MCPRegistration, resolve_config

PluginMCPConnector = Callable[
    [PluginContext, MCPRegistration, Mapping[str, object], dict[str, Any], AsyncExitStack],
    Awaitable[MCPServer],
]


async def connect_plugin_mcp(
    context: PluginContext,
    registration: MCPRegistration,
    config: Mapping[str, object],
    options: dict[str, Any],
    lifespan: AsyncExitStack,
) -> MCPServer:
    defaults = context.config.get("mcp", {})
    if not isinstance(defaults, dict) or not isinstance(defaults.get(registration.name, {}), dict):
        raise ValueError("Installed MCP configuration must be an object")
    values = resolve_config(
        registration.config_fields, {**defaults.get(registration.name, {}), **config}
    )
    server = registration.factory(values, **options)
    if inspect.isawaitable(server):
        server = await server
    if not isinstance(server, MCPServer):
        raise TypeError("Plugin MCP factory must return a fresh native MCPServer")
    # Factories forward native_options to the SDK constructor, which normalizes
    # approval policies. Setting an attribute afterward would not update that policy.
    connect: Callable[[], Awaitable[None]] = server.connect
    cleanup: Callable[[], Awaitable[None]] = server.cleanup
    try:
        await connect()
    except BaseException:
        await cleanup()
        raise
    lifespan.push_async_callback(cleanup)
    return server
