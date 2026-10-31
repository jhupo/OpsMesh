"""Actor-aware skill publication, inspection and workspace materialization."""

from pathlib import Path
from typing import Literal

from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import ResourceAuthorization, VersionAvailability
from opsmesh.modules.workspaces.api import WorkspaceArtifact, WorkspaceAuthorization

from .api import GitSkillSource, SkillDefinition
from .internal.skills import PersistentSkillCatalog
from .management import CapabilityManagement


class SkillManagement:
    def __init__(
        self,
        catalog: PersistentSkillCatalog,
        capabilities: CapabilityManagement,
        authorize: ResourceAuthorization,
        authorize_workspace: WorkspaceAuthorization,
        audit: AuditJournal,
    ) -> None:
        self._catalog, self._capabilities = catalog, capabilities
        self._authorize, self._workspace, self._audit = authorize, authorize_workspace, audit

    async def get(self, reference: str, *, actor_id: str) -> SkillDefinition:
        await self._authorize(actor_id, "capability:read", reference)
        return await self._catalog.get(reference, include_disabled=True)

    async def list(self, *, actor_id: str) -> tuple[SkillDefinition, ...]:
        await self._authorize(actor_id, "capability:read", "*")
        return await self._catalog.list()

    async def publish(
        self, name: str, version: str, source: Path, *, actor_id: str, description: str = ""
    ) -> SkillDefinition:
        return await self._capabilities.publish_skill(
            name, version, source, actor_id=actor_id, description=description
        )

    async def publish_git(
        self,
        name: str,
        version: str,
        source: GitSkillSource,
        *,
        actor_id: str,
        description: str = "",
    ) -> SkillDefinition:
        return await self._capabilities.publish_git(
            name, version, source, actor_id=actor_id, description=description
        )

    async def load(
        self, reference: str, workspace_id: str, *, actor_id: str, target: str = ".agents"
    ) -> tuple[WorkspaceArtifact, ...]:
        await self._authorize(actor_id, "capability:use", f"skill:{reference}")
        await self._workspace(actor_id, workspace_id, write=True)
        async with audited_operation(self._audit, "skill.load", reference, actor_id):
            return await self._catalog.load(reference, workspace_id, target=target)

    async def prepare(
        self,
        references: tuple[str, ...],
        workspace_id: str,
        *,
        actor_id: str,
        mode: Literal["eager", "lazy"] = "eager",
    ) -> tuple[SkillDefinition, ...]:
        await self._workspace(actor_id, workspace_id, write=True)
        for reference in references:
            await self._authorize(actor_id, "capability:use", f"skill:{reference}")
        async with audited_operation(self._audit, "skill.prepare", workspace_id, actor_id):
            return await self._catalog.prepare(references, workspace_id, mode=mode)

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
            "skill",
            reference,
            enabled,
            actor_id=actor_id,
            expected_revision=expected_revision,
            reason=reason,
        )
