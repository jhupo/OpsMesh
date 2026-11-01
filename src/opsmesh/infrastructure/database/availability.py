"""CAS persistence for enablement of immutable, module-owned resource versions."""

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.resource_contracts import VersionAvailability

from . import AsyncDatabase, VersionedRecordStore


class PersistentVersionAvailability:
    def __init__(self, database: AsyncDatabase, kind: str) -> None:
        self._records = VersionedRecordStore(database, f"{kind}_availability")

    async def get(self, reference: str) -> VersionAvailability:
        row = await self._records.get(reference)
        return (
            VersionAvailability.model_validate({**row.data, "revision": row.revision})
            if row
            else VersionAvailability(reference=reference)
        )

    async def require_enabled(self, reference: str) -> None:
        if not (await self.get(reference)).enabled:
            raise OpsMeshError("RESOURCE_DISABLED", "Resource version is disabled")

    async def set_enabled(
        self,
        reference: str,
        enabled: bool,
        *,
        actor_id: str,
        expected_revision: int,
        reason: str = "",
    ) -> VersionAvailability:
        value = VersionAvailability(
            reference=reference,
            enabled=enabled,
            actor_id=actor_id,
            reason=reason,
        )
        data = value.model_dump(mode="json", exclude={"revision"})
        row = (
            await self._records.create(reference, data)
            if expected_revision == 0
            else await self._records.update(reference, data, expected_revision)
        )
        return VersionAvailability.model_validate({**row.data, "revision": row.revision})
