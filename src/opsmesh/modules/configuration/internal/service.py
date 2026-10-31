"""Platform configuration with revision checks and typed admission policies."""

from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.configuration.api import AdmissionPolicy, ConfigurationEntry


class PlatformConfigurationService:
    def __init__(self, database: AsyncDatabase) -> None:
        self._store = VersionedRecordStore(database, "configuration_settings")

    async def get(self, key: str) -> ConfigurationEntry | None:
        row = await self._store.get(key)
        return ConfigurationEntry(key=key, value=row.data, revision=row.revision) if row else None

    async def list(self) -> list[ConfigurationEntry]:
        return [
            ConfigurationEntry(key=row.id, value=row.data, revision=row.revision)
            for row in await self._store.list()
        ]

    async def set(
        self, key: str, value: dict[str, Any], expected_revision: int | None = None
    ) -> ConfigurationEntry:
        if key == "admission":
            value = AdmissionPolicy.model_validate(value).model_dump(mode="json")
        if any(word in key.casefold() for word in ("password", "secret", "token")):
            raise OpsMeshError("CONFIG_INVALID", "Credentials belong in the secret provider")
        events = [
            {"type": "configuration.updated", "source": "configuration", "payload": {"key": key}}
        ]
        row = (
            await self._store.create(key, value, events=events)
            if expected_revision is None
            else await self._store.update(key, value, expected_revision, events=events)
        )
        return ConfigurationEntry(key=key, value=row.data, revision=row.revision)

    async def admission_policy(self) -> AdmissionPolicy:
        entry = await self.get("admission")
        return AdmissionPolicy.model_validate(entry.value) if entry else AdmissionPolicy()
