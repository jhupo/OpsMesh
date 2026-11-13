"""Declare optional bot connections and own shared MCP resources."""

from agents.mcp import MCPServerManager, MCPServerStreamableHttp

from opsmesh import on_shutdown, on_startup

from . import agent as definition
from . import channels
from .config import secret

_connections: MCPServerManager | None = None


@on_startup
async def connect_mcp() -> None:
    """Connect optional tools before the plugin becomes ready."""
    global _connections
    settings = definition.settings
    if settings.qq is not None:
        channels.qq.declare_connection(settings.qq, name="primary")
    if settings.dingtalk is not None:
        channels.dingtalk.declare_connection(settings.dingtalk, name="internal")
    if settings.mcp_url is None:
        return
    headers = {}
    if settings.mcp_token_ref is not None:
        headers["Authorization"] = f"Bearer {secret(settings.mcp_token_ref)}"
    server = MCPServerStreamableHttp(
        name="chat-tools",
        params={"url": str(settings.mcp_url), "headers": headers},
        cache_tools_list=True,
    )
    _connections = MCPServerManager(
        [server],
        strict=True,
        connect_in_parallel=True,
        suppress_cancelled_error=False,
    )
    await _connections.connect_all()
    definition.agent.mcp_servers = _connections.active_servers


@on_shutdown
async def disconnect_mcp() -> None:
    """Close shared tools after channels have drained their handlers."""
    global _connections
    if _connections is not None:
        await _connections.cleanup_all()
        _connections = None
    if hasattr(definition, "agent"):
        definition.agent.mcp_servers = []
