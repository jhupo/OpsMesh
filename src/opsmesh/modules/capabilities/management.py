"""Protected administration of immutable capability definitions and availability."""

from typing import Literal

from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import (
    ResourceAuthorization,
    ResourceReference,
    ResourceReferenceLookup,
    VersionAvailability,
)

from .api import (
    MCPServerDefinition,
    ScriptToolDefinition,
    SkillDefinition,
    ToolDefinition,
)
from .internal.availability_management import set_capability_enabled
from .internal.catalog import PersistentCapabilityCatalog
from .mcp_management import MCPManagement
from .script_management import ScriptManagement
from .skill_management import SkillManagement

CapabilityKind = Literal["tool", "skill", "mcp", "script"]
CapabilityDefinition = ToolDefinition | SkillDefinition | MCPServerDefinition | ScriptToolDefinition


class CapabilityManagement:
    def __init__(
        self,
        tools: PersistentCapabilityCatalog,
        skills: SkillManagement,
        mcp: MCPManagement,
        scripts: ScriptManagement,
        authorize: ResourceAuthorization,
        audit: AuditJournal,
        *,
        references: ResourceReferenceLookup,
    ) -> None:
        self._tools, self._skills, self._mcp, self._scripts = tools, skills, mcp, scripts
        self._authorize, self._audit = authorize, audit
        self._references = references

    async def get(
        self,
        kind: CapabilityKind,
        reference: str,
        *,
        actor_id: str,
    ) -> CapabilityDefinition:
        if kind == "tool":
            return await self.get_tool(reference, actor_id=actor_id)
        return await self._catalog(kind).get(reference, actor_id=actor_id)

    def _catalog(
        self, kind: Literal["skill", "mcp", "script"]
    ) -> SkillManagement | MCPManagement | ScriptManagement:
        if kind == "skill":
            return self._skills
        if kind == "mcp":
            return self._mcp
        return self._scripts

    async def list(
        self, kind: CapabilityKind, *, actor_id: str
    ) -> tuple[CapabilityDefinition, ...]:
        return (
            await self.list_tools(actor_id=actor_id)
            if kind == "tool"
            else await self._catalog(kind).list(actor_id=actor_id)
        )

    async def availability(
        self,
        kind: CapabilityKind,
        reference: str,
        *,
        actor_id: str,
    ) -> VersionAvailability:
        if kind == "tool":
            await self.get_tool(reference, actor_id=actor_id)
            return await self._tools.availability.get(reference)
        return await self._catalog(kind).availability(reference, actor_id=actor_id)

    async def set_enabled(
        self,
        kind: CapabilityKind,
        reference: str,
        enabled: bool,
        *,
        actor_id: str,
        expected_revision: int,
        reason: str = "",
    ) -> VersionAvailability:
        if kind != "tool":
            return await self._catalog(kind).set_enabled(
                reference,
                enabled,
                actor_id=actor_id,
                expected_revision=expected_revision,
                reason=reason,
            )
        return await set_capability_enabled(
            "tool",
            reference,
            enabled,
            actor_id=actor_id,
            expected_revision=expected_revision,
            reason=reason,
            authorize=self._authorize,
            audit=self._audit,
            availability=self._tools.availability,
            read=lambda: self.get_tool(reference, actor_id=actor_id),
        )

    async def get_tool(self, reference: str, *, actor_id: str) -> ToolDefinition:
        await self._authorize(actor_id, "capability:read", reference)
        return await self._tools.get_tool(reference, include_disabled=True)

    async def list_tools(self, *, actor_id: str) -> tuple[ToolDefinition, ...]:
        await self._authorize(actor_id, "capability:read", "*")
        return await self._tools.list_tools()

    async def register_tool(self, definition: ToolDefinition, *, actor_id: str) -> ToolDefinition:
        await self._authorize(actor_id, "capability:manage", definition.ref)
        async with audited_operation(self._audit, "tool.publish", definition.ref, actor_id):
            return await self._tools.register_tool(definition)

    async def references(
        self, kind: CapabilityKind, reference: str, *, actor_id: str
    ) -> tuple[ResourceReference, ...]:
        await self.get(kind, reference, actor_id=actor_id)
        return await self._references(actor_id, kind, reference)
