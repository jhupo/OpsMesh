"""Actor-aware model administration; runtime receives the internal read catalog."""

from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import (
    ResourceAuthorization,
    ResourceReference,
    ResourceReferenceLookup,
    VersionAvailability,
)

from .api import ModelProfile
from .internal.service import ModelProfileCatalog


class ModelManagement:
    def __init__(
        self,
        catalog: ModelProfileCatalog,
        authorize: ResourceAuthorization,
        audit: AuditJournal,
        *,
        references: ResourceReferenceLookup,
    ) -> None:
        self._catalog, self._authorize, self._audit = catalog, authorize, audit
        self._references = references

    async def create(self, profile: ModelProfile, *, actor_id: str) -> ModelProfile:
        await self._authorize(actor_id, "model:manage", profile.id)
        async with audited_operation(self._audit, "model.create", profile.id, actor_id):
            return await self._catalog.create(profile)

    async def update(
        self,
        profile: ModelProfile,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> ModelProfile:
        await self._authorize(actor_id, "model:manage", profile.id)
        async with audited_operation(self._audit, "model.update", profile.id, actor_id):
            return await self._catalog.update(profile, expected_revision)

    async def get(self, reference: str, *, actor_id: str) -> ModelProfile:
        await self._authorize(actor_id, "model:read", reference)
        return await self._catalog.get(reference, include_disabled=True)

    async def list(self, *, actor_id: str) -> list[ModelProfile]:
        await self._authorize(actor_id, "model:read", "*")
        return await self._catalog.list()

    async def versions(self, profile_id: str, *, actor_id: str) -> tuple[ModelProfile, ...]:
        await self._authorize(actor_id, "model:read", profile_id)
        return await self._catalog.versions(profile_id)

    async def availability(self, reference: str, *, actor_id: str) -> VersionAvailability:
        profile = await self.get(reference, actor_id=actor_id)
        return await self._catalog.availability.get(f"{profile.id}@{profile.revision}")

    async def set_enabled(
        self,
        reference: str,
        enabled: bool,
        *,
        actor_id: str,
        expected_revision: int,
        reason: str = "",
    ) -> VersionAvailability:
        await self._authorize(actor_id, "model:manage", reference)
        profile = await self._catalog.get(reference, include_disabled=True)
        pinned = f"{profile.id}@{profile.revision}"
        async with audited_operation(self._audit, "model.availability", pinned, actor_id):
            return await self._catalog.availability.set_enabled(
                pinned,
                enabled,
                actor_id=actor_id,
                expected_revision=expected_revision,
                reason=reason,
            )

    async def references(self, reference: str, *, actor_id: str) -> tuple[ResourceReference, ...]:
        await self.get(reference, actor_id=actor_id)
        return await self._references(actor_id, "model", reference)
