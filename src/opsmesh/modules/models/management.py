"""Actor-aware model administration; runtime receives the internal read catalog."""

import builtins
import hashlib

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import (
    ResourceAuthorization,
    ResourceReference,
    ResourceReferenceLookup,
    VersionAvailability,
)

from .api import ModelProfile
from .credentials import ModelCredentialManagement
from .internal.service import ModelProfileCatalog
from .validation import validate_personal_endpoint


class DefaultModel(OpsMeshContract):
    reference: str | None = None
    revision: int = 0


class ModelManagement:
    def __init__(
        self,
        catalog: ModelProfileCatalog,
        authorize: ResourceAuthorization,
        audit: AuditJournal,
        *,
        references: ResourceReferenceLookup,
        accounts: AccountDirectory | None = None,
        credentials: ModelCredentialManagement | None = None,
        personal_model_allowed_hosts: tuple[str, ...] = ("api.openai.com",),
    ) -> None:
        self._catalog, self._authorize, self._audit = catalog, authorize, audit
        self._references = references
        self._accounts, self.credentials = accounts, credentials
        self._personal_model_allowed_hosts = personal_model_allowed_hosts

    async def create(self, profile: ModelProfile, *, actor_id: str) -> ModelProfile:
        if profile.owner_id is not None:
            raise OpsMeshError("INVALID_REQUEST", "Use the personal model API for owned models")
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
        current = await self._catalog.get(profile.id, include_disabled=True)
        if current.owner_id or profile.owner_id:
            raise OpsMeshError("FORBIDDEN", "Use the personal model API for owned models")
        await self._authorize(actor_id, "model:manage", profile.id)
        async with audited_operation(self._audit, "model.update", profile.id, actor_id):
            return await self._catalog.update(profile, expected_revision)

    async def get(self, reference: str, *, actor_id: str) -> ModelProfile:
        value = await self._catalog.get(reference, include_disabled=True)
        await self.authorize_profile(value, actor_id=actor_id, action="model:read")
        return value

    async def list(self, *, actor_id: str) -> list[ModelProfile]:
        await self._authorize(actor_id, "model:read", "*")
        return [item for item in await self._catalog.list() if item.owner_id is None]

    async def versions(self, profile_id: str, *, actor_id: str) -> tuple[ModelProfile, ...]:
        await self.get(profile_id, actor_id=actor_id)
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
        profile = await self._catalog.get(reference, include_disabled=True)
        await self.authorize_profile(profile, actor_id=actor_id, action="model:manage")
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

    async def _active(self, actor_id: str) -> None:
        if self._accounts is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Personal model management unavailable")
        if not (await self._accounts.get_principal(actor_id)).active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")

    async def authorize_profile(self, profile: ModelProfile, *, actor_id: str, action: str) -> None:
        if profile.owner_id is not None:
            await self._active(actor_id)
            if profile.owner_id != actor_id:
                raise OpsMeshError("FORBIDDEN", "Model belongs to another user")
            validate_personal_endpoint(profile, self._personal_model_allowed_hosts)
        else:
            await self._authorize(actor_id, action, f"{profile.id}@{profile.revision}")

    async def personal_profiles(self, *, actor_id: str) -> builtins.list[ModelProfile]:
        await self._active(actor_id)
        return [item for item in await self._catalog.list() if item.owner_id == actor_id]

    async def save_personal(
        self, profile: ModelProfile, *, actor_id: str, expected_revision: int | None = None
    ) -> ModelProfile:
        await self._active(actor_id)
        prefix = "user-" + hashlib.sha256(actor_id.encode()).hexdigest()[:16] + "-"
        identifier = profile.id if profile.id.startswith(prefix) else prefix + profile.id
        if len(identifier) > 100 or "@" in identifier:
            raise OpsMeshError(
                "INVALID_REQUEST", "Personal model identifier is too long or invalid"
            )
        value = ModelProfile.model_validate(
            {**profile.model_dump(), "id": identifier, "owner_id": actor_id}
        )
        validate_personal_endpoint(value, self._personal_model_allowed_hosts)
        if self.credentials is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Personal credentials unavailable")
        await self.credentials.validate_reference(value.secret_ref, actor_id=actor_id)
        async with audited_operation(self._audit, "model.personal.save", identifier, actor_id):
            if expected_revision is None:
                return await self._catalog.create(value)
            current = await self._catalog.get(identifier, include_disabled=True)
            if current.owner_id != actor_id:
                raise OpsMeshError("FORBIDDEN", "Model belongs to another user")
            return await self._catalog.update(value, expected_revision)

    async def default(self, *, actor_id: str, personal: bool = False) -> DefaultModel:
        if personal:
            await self._active(actor_id)
        else:
            await self._authorize(actor_id, "model:read", "*")
        key = f"user:{actor_id}" if personal else "platform"
        row = await self._catalog.preferences.get(key)
        return (
            DefaultModel(reference=row.data.get("reference"), revision=row.revision)
            if row
            else DefaultModel()
        )

    async def set_default(
        self,
        reference: str | None,
        expected_revision: int,
        *,
        actor_id: str,
        personal: bool = False,
    ) -> DefaultModel:
        if personal:
            await self._active(actor_id)
        else:
            await self._authorize(actor_id, "model:manage", "*")
        if reference:
            if "@" not in reference:
                raise OpsMeshError("CONFIG_INVALID", "Default model requires an immutable revision")
            profile = await self._catalog.get(reference)
            await self.authorize_profile(profile, actor_id=actor_id, action="model:use")
            if not personal and profile.owner_id:
                raise OpsMeshError("FORBIDDEN", "Platform default must be a shared model")
        key = f"user:{actor_id}" if personal else "platform"
        async with audited_operation(self._audit, "model.default", key, actor_id):
            if expected_revision == 0:
                row = await self._catalog.preferences.create(key, {"reference": reference})
            else:
                row = await self._catalog.preferences.update(
                    key, {"reference": reference}, expected_revision
                )
        return DefaultModel(reference=reference, revision=row.revision)
