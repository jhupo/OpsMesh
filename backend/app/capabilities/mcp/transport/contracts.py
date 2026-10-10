from typing import Protocol, runtime_checkable

from backend.app.capabilities.mcp.execution.prepared import McpOperation
from backend.app.capabilities.mcp.models import McpCredentialReference, McpServer


class McpToolAdapter(Protocol):
    def prepare(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> McpOperation:
        """Prepare a detached operation without performing remote I/O."""


@runtime_checkable
class McpToolAdapterResolver(Protocol):
    def resolve(self, server: McpServer) -> McpToolAdapter: ...
