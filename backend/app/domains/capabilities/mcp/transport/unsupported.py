from __future__ import annotations

from dataclasses import dataclass

from backend.app.domains.capabilities.mcp.execution.contracts import McpExecutionError
from backend.app.domains.capabilities.mcp.models import (
    McpCredentialReference,
    McpServer,
)


@dataclass(frozen=True)
class UnsupportedMcpToolAdapter:
    server_type: str

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
            f"MCP server type is not configured for direct execution: {self.server_type}",
            code="mcp_adapter_unsupported",
        )
