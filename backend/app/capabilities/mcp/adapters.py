from typing import Protocol, runtime_checkable

from backend.app.capabilities.mcp.types import McpExecutionError
from backend.app.capabilities.models import McpCredentialReference, McpServer


class McpToolAdapter(Protocol):
    async def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        """Execute an MCP tool and return a JSON-serializable response."""


@runtime_checkable
class McpToolAdapterResolver(Protocol):
    def resolve(self, server: McpServer) -> McpToolAdapter: ...


class UnconfiguredMcpToolAdapter:
    async def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        raise McpExecutionError(
            "MCP protocol adapter is not configured",
            code="mcp_adapter_unconfigured",
        )
