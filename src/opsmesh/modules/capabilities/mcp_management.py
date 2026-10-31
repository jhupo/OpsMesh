"""Protected MCP catalog administration and connector execution."""

from typing import Any

from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import ResourceAuthorization, VersionAvailability

from .api import MCPClient, MCPServerDefinition, ToolDefinition
from .internal.mcp import MCPServerRegistry
from .management import CapabilityManagement


class MCPManagement:
    def __init__(
        self,
        catalog: MCPServerRegistry,
        client: MCPClient,
        capabilities: CapabilityManagement,
        authorize: ResourceAuthorization,
        audit: AuditJournal,
    ) -> None:
        self._catalog, self._client, self._capabilities = catalog, client, capabilities
        self._authorize, self._audit = authorize, audit

    async def register(
        self, definition: MCPServerDefinition, *, actor_id: str
    ) -> MCPServerDefinition:
        return await self._capabilities.register_mcp(definition, actor_id=actor_id)

    async def get(self, reference: str, *, actor_id: str) -> MCPServerDefinition:
        await self._authorize(actor_id, "capability:read", reference)
        return await self._catalog.get(reference, include_disabled=True)

    async def list(self, *, actor_id: str) -> tuple[MCPServerDefinition, ...]:
        await self._authorize(actor_id, "capability:read", "*")
        return await self._catalog.list()

    async def discover(self, reference: str, *, principal_id: str) -> tuple[ToolDefinition, ...]:
        definition = await self._catalog.get(reference)
        return await self._client.discover(definition, principal_id=principal_id)

    async def call(
        self, reference: str, tool_name: str, arguments: dict[str, Any], *, principal_id: str
    ) -> Any:
        definition = await self._catalog.get(reference)
        async with audited_operation(self._audit, "mcp.call", reference, principal_id):
            return await self._client.call(
                definition, tool_name, arguments, principal_id=principal_id
            )

    async def set_enabled(
        self,
        reference: str,
        enabled: bool,
        *,
        actor_id: str,
        expected_revision: int,
        reason: str = "",
    ) -> VersionAvailability:
        return await self._capabilities.set_enabled(
            "mcp",
            reference,
            enabled,
            actor_id=actor_id,
            expected_revision=expected_revision,
            reason=reason,
        )
