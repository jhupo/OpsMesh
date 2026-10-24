"""Delegate connection ownership and per-binding filtering to the connection owner."""

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from agents.mcp import MCPServer

from opsmesh.agents.callbacks import CallbackRegistry
from opsmesh.agents.config.callbacks import CallbackRef
from opsmesh.agents.config.mcp import MCPServerRef

# The resolver must return a connected binding-local view, enforce these native
# options, and register any owned cleanup on lifespan. Borrowed connections stay open.
MCPResolver = Callable[[MCPServerRef, dict[str, Any], AsyncExitStack], Awaitable[MCPServer]]


async def resolve_mcp(
    reference: MCPServerRef,
    *,
    resolver: MCPResolver | None,
    callbacks: CallbackRegistry,
    lifespan: AsyncExitStack,
) -> MCPServer:
    if resolver is None:
        raise ValueError("MCP references require an authorized connection resolver")
    options: dict[str, Any] = {}
    if reference.allowed_tools is not None:
        options["tool_filter"] = {"allowed_tool_names": list(reference.allowed_tools)}
    elif reference.blocked_tools:
        options["tool_filter"] = {"blocked_tool_names": list(reference.blocked_tools)}
    if "require_approval" in reference.model_fields_set:
        options["require_approval"] = (
            callbacks.resolve("tool_approval", reference.require_approval)
            if isinstance(reference.require_approval, CallbackRef)
            else reference.require_approval
        )
    server = await resolver(reference.model_copy(deep=True), options, lifespan)
    if not isinstance(server, MCPServer):
        raise TypeError("MCP resolver must return a native MCPServer")
    return server
