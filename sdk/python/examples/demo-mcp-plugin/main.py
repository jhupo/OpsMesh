"""Generic transport example; endpoint and credentials come from host configuration."""

from collections.abc import Mapping
from typing import Any

from agents.mcp import MCPServerStreamableHttp
from opsmesh_sdk import PluginContext


def create_server(config: Mapping[str, object], **native_options: Any) -> MCPServerStreamableHttp:
    token = config.get("token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return MCPServerStreamableHttp(
        name="demo-mcp-plugin/service",
        params={"url": str(config["url"]), "headers": headers},
        **native_options,
    )


def setup(ctx: PluginContext) -> None:
    ctx.mcp(
        "service",
        create_server,
        description="Configured MCP service",
        config_fields=[
            {"name": "url", "label": "Endpoint", "type": "text", "required": True},
            {"name": "token", "label": "Token", "type": "password"},
        ],
    )
