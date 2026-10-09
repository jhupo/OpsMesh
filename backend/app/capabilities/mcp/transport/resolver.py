from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.capabilities.mcp.managed_runtime import ManagedMcpToolAdapter
from backend.app.capabilities.mcp.models import McpServer
from backend.app.capabilities.mcp.transport.contracts import McpToolAdapter
from backend.app.capabilities.mcp.transport.remote import (
    HostedMcpToolAdapter,
    SseMcpToolAdapter,
    StreamableHttpMcpToolAdapter,
)
from backend.app.capabilities.mcp.transport.unsupported import UnsupportedMcpToolAdapter
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.shared.security.secrets import SecretEncryptionService


@dataclass(frozen=True)
class McpAdapterResolver:
    secret_service: SecretEncryptionService | None = None
    session: Session | None = None
    docker_client: DockerRuntimeClient | None = None

    def resolve(self, server: McpServer) -> McpToolAdapter:
        server_type = server.server_type.lower().strip()
        if server_type == "streamable_http":
            return StreamableHttpMcpToolAdapter(secret_service=self.secret_service)
        if server_type == "sse":
            return SseMcpToolAdapter(secret_service=self.secret_service)
        if server_type == "hosted":
            return HostedMcpToolAdapter(secret_service=self.secret_service)
        if server_type == "stdio":
            if (
                server.connection.get("runtime") == "managed"
                and self.session is not None
                and self.docker_client is not None
            ):
                return ManagedMcpToolAdapter(self.session, self.docker_client)
            return UnsupportedMcpToolAdapter(server_type=server_type)
        return UnsupportedMcpToolAdapter(server_type=server.server_type)
