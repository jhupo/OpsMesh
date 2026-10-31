"""Protected script versions with explicit execution identity."""

from pathlib import Path
from typing import Any

from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import ResourceAuthorization, VersionAvailability

from .api import ScriptToolDefinition, ScriptToolSource
from .internal.scripts import PersistentScriptTools
from .management import CapabilityManagement


class ScriptManagement:
    def __init__(
        self,
        scripts: PersistentScriptTools,
        capabilities: CapabilityManagement,
        authorize: ResourceAuthorization,
        audit: AuditJournal,
    ) -> None:
        self._scripts, self._capabilities, self._authorize, self._audit = (
            scripts,
            capabilities,
            authorize,
            audit,
        )

    async def publish(
        self, source: Path, declaration: ScriptToolSource, *, actor_id: str
    ) -> ScriptToolDefinition:
        return await self._capabilities.publish_script(source, declaration, actor_id=actor_id)

    async def get(self, reference: str, *, actor_id: str) -> ScriptToolDefinition:
        await self._authorize(actor_id, "capability:read", reference)
        return await self._scripts.get(reference, include_disabled=True)

    async def list(self, *, actor_id: str) -> tuple[ScriptToolDefinition, ...]:
        await self._authorize(actor_id, "capability:read", "*")
        return await self._scripts.list()

    async def execute(self, reference: str, arguments: dict[str, Any], *, principal_id: str) -> Any:
        await self._authorize(principal_id, "tool:invoke", reference)
        async with audited_operation(self._audit, "script.execute", reference, principal_id):
            return await self._scripts.execute(reference, arguments, principal_id=principal_id)

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
            "script",
            reference,
            enabled,
            actor_id=actor_id,
            expected_revision=expected_revision,
            reason=reason,
        )
