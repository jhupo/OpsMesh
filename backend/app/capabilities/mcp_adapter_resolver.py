from __future__ import annotations

from dataclasses import dataclass

from backend.app.capabilities.mcp.adapters import McpToolAdapter
from backend.app.capabilities.mcp_remote_adapters import (
    HostedMcpToolAdapter,
    SseMcpToolAdapter,
    StreamableHttpMcpToolAdapter,
)
from backend.app.capabilities.mcp_unsupported_adapter import UnsupportedMcpToolAdapter
from backend.app.capabilities.models import McpServer
from backend.app.secrets.service import SecretEncryptionService


@dataclass(frozen=True)
class McpAdapterResolver:
    secret_service: SecretEncryptionService | None = None

    def resolve(self, server: McpServer) -> McpToolAdapter:
        server_type = server.server_type.lower().strip()
        if server_type == "streamable_http":
            return StreamableHttpMcpToolAdapter(secret_service=self.secret_service)
        if server_type == "sse":
            return SseMcpToolAdapter(secret_service=self.secret_service)
        if server_type == "hosted":
            return HostedMcpToolAdapter(secret_service=self.secret_service)
        if server_type == "stdio":
            return UnsupportedMcpToolAdapter(server_type=server_type)
        return UnsupportedMcpToolAdapter(server_type=server.server_type)
