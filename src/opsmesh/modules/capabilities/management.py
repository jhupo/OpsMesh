"""Protected administration of immutable capability definitions and availability."""

from pathlib import Path
from typing import Literal

from opsmesh.infrastructure.database.availability import PersistentVersionAvailability
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import (
    ResourceAuthorization,
    ResourceReference,
    ResourceReferenceLookup,
    VersionAvailability,
)

from .api import (
    GitSkillSource,
    MCPServerDefinition,
    ScriptToolDefinition,
    ScriptToolSource,
    SkillDefinition,
    ToolDefinition,
)
from .internal.catalog import PersistentCapabilityCatalog
from .internal.mcp import MCPServerRegistry
from .internal.scripts import PersistentScriptTools
from .internal.skills import PersistentSkillCatalog

CapabilityKind = Literal["tool", "skill", "mcp", "script"]
CapabilityDefinition = ToolDefinition | SkillDefinition | MCPServerDefinition | ScriptToolDefinition


class CapabilityManagement:
    def __init__(
        self,
        tools: PersistentCapabilityCatalog,
        skills: PersistentSkillCatalog,
        mcp: MCPServerRegistry,
        scripts: PersistentScriptTools,
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
        await self._authorize(actor_id, "capability:read", reference)
        if kind == "tool":
            return await self._tools.get_tool(reference, include_disabled=True)
        return await self._catalog(kind).get(reference, include_disabled=True)

    def _catalog(
        self, kind: Literal["skill", "mcp", "script"]
    ) -> PersistentSkillCatalog | MCPServerRegistry | PersistentScriptTools:
        if kind == "skill":
            return self._skills
        if kind == "mcp":
            return self._mcp
        return self._scripts

    def _availability(self, kind: CapabilityKind) -> PersistentVersionAvailability:
        return self._tools.availability if kind == "tool" else self._catalog(kind).availability

    async def list(
        self, kind: CapabilityKind, *, actor_id: str
    ) -> tuple[CapabilityDefinition, ...]:
        await self._authorize(actor_id, "capability:read", "*")
        return (
            await self._tools.list_tools() if kind == "tool" else await self._catalog(kind).list()
        )

    async def availability(
        self,
        kind: CapabilityKind,
        reference: str,
        *,
        actor_id: str,
    ) -> VersionAvailability:
        await self.get(kind, reference, actor_id=actor_id)
        return await self._availability(kind).get(reference)

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
        await self._authorize(actor_id, "capability:manage", reference)
        await self.get(kind, reference, actor_id=actor_id)
        async with audited_operation(self._audit, f"{kind}.availability", reference, actor_id):
            return await self._availability(kind).set_enabled(
                reference,
                enabled,
                actor_id=actor_id,
                expected_revision=expected_revision,
                reason=reason,
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

    async def register_mcp(
        self,
        definition: MCPServerDefinition,
        *,
        actor_id: str,
    ) -> MCPServerDefinition:
        reference = f"{definition.name}@{definition.version}"
        await self._authorize(actor_id, "capability:manage", reference)
        async with audited_operation(self._audit, "mcp.publish", reference, actor_id):
            return await self._mcp.register(definition)

    async def publish_skill(
        self,
        name: str,
        version: str,
        source: Path,
        *,
        actor_id: str,
        description: str = "",
    ) -> SkillDefinition:
        reference = f"{name}@{version}"
        await self._authorize(actor_id, "capability:manage", reference)
        async with audited_operation(self._audit, "skill.publish", reference, actor_id):
            return await self._skills.publish(name, version, source, description=description)

    async def publish_git(
        self,
        name: str,
        version: str,
        source: GitSkillSource,
        *,
        actor_id: str,
        description: str = "",
    ) -> SkillDefinition:
        reference = f"{name}@{version}"
        await self._authorize(actor_id, "capability:manage", reference)
        async with audited_operation(self._audit, "skill.publish_git", reference, actor_id):
            return await self._skills.publish_git(name, version, source, description=description)

    async def publish_script(
        self,
        source: Path,
        declaration: ScriptToolSource,
        *,
        actor_id: str,
    ) -> ScriptToolDefinition:
        reference = f"{declaration.name}@{declaration.version}"
        await self._authorize(actor_id, "capability:manage", reference)
        async with audited_operation(self._audit, "script.publish", reference, actor_id):
            return await self._scripts.publish(source, declaration)

    async def references(
        self, kind: CapabilityKind, reference: str, *, actor_id: str
    ) -> tuple[ResourceReference, ...]:
        await self.get(kind, reference, actor_id=actor_id)
        return await self._references(actor_id, kind, reference)
