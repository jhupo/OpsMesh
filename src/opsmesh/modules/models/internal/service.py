"""Versioned model profiles and explicit compatibility validation."""

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.availability import PersistentVersionAvailability
from opsmesh.modules.models.api import ModelProfile, validate_model_profile


class ModelProfileCatalog:
    def __init__(self, database: AsyncDatabase) -> None:
        self.store = VersionedRecordStore(database, "models_profiles")
        self.availability = PersistentVersionAvailability(database, "models_profiles")
        self.preferences = VersionedRecordStore(database, "models_preferences")

    async def default_reference(self, owner_id: str) -> str | None:
        personal = await self.preferences.get(f"user:{owner_id}")
        if personal and personal.data.get("reference"):
            return str(personal.data["reference"])
        platform = await self.preferences.get("platform")
        return (
            str(platform.data["reference"]) if platform and platform.data.get("reference") else None
        )

    async def create(self, profile: ModelProfile) -> ModelProfile:
        validate_model_profile(profile)
        if "@" in profile.id or profile.revision != 1:
            raise OpsMeshError("CONFIG_INVALID", "New model profiles start at revision 1")
        data = profile.model_dump(mode="json")
        await self.store.create(profile.id, {"current": data, "versions": {"1": data}})
        return profile

    async def get(self, profile_id: str, *, include_disabled: bool = False) -> ModelProfile:
        identifier, separator, requested_revision = profile_id.rpartition("@")
        row = await self.store.get(identifier if separator else profile_id)
        if row is None:
            raise OpsMeshError("not_found", "Model profile does not exist")
        try:
            data = row.data["versions"][requested_revision] if separator else row.data["current"]
        except KeyError as error:
            raise OpsMeshError("NOT_FOUND", "Model profile revision does not exist") from error
        profile = ModelProfile.model_validate(data)
        if not include_disabled:
            await self.availability.require_enabled(f"{profile.id}@{profile.revision}")
        return profile

    async def list(self) -> list[ModelProfile]:
        return [ModelProfile.model_validate(row.data["current"]) for row in await self.store.list()]

    async def get_definition(self, profile_id: str) -> ModelProfile:
        """Management and accounting may reference a withdrawn immutable version."""
        return await self.get(profile_id, include_disabled=True)

    async def versions(self, profile_id: str) -> tuple[ModelProfile, ...]:
        row = await self.store.get(profile_id)
        if row is None:
            raise OpsMeshError("NOT_FOUND", "Model profile does not exist")
        return tuple(ModelProfile.model_validate(value) for value in row.data["versions"].values())

    async def update(self, profile: ModelProfile, expected_revision: int) -> ModelProfile:
        validate_model_profile(profile)
        prior = await self.store.get(profile.id)
        if prior is None:
            raise OpsMeshError("NOT_FOUND", "Model profile does not exist")
        if profile.owner_id != prior.data["current"].get("owner_id"):
            raise OpsMeshError("FORBIDDEN", "Model ownership cannot change")
        profile = profile.model_copy(update={"revision": expected_revision + 1})
        data = profile.model_dump(mode="json")
        await self.store.update(
            profile.id,
            {
                "current": data,
                "versions": {**prior.data["versions"], str(profile.revision): data},
            },
            expected_revision,
        )
        return profile

    async def require_features(self, profile_id: str, required: set[str]) -> ModelProfile:
        profile = await self.get(profile_id)
        missing = required - profile.features
        if missing:
            raise OpsMeshError(
                "feature_unsupported",
                "Model profile lacks required capabilities",
                details={"missing": sorted(missing)},
            )
        return profile
