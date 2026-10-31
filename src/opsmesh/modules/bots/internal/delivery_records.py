"""Persistent idempotency claims shared by ingress and outbound delivery."""

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore


class BotDeliveryRecords:
    def __init__(self, store: VersionedRecordStore) -> None:
        self.store = store

    async def claim(self, record_id: str, data: JsonData) -> tuple[StoredRecord, bool]:
        try:
            return await self.store.create(record_id, data), True
        except OpsMeshError as exc:
            if exc.code.upper() != "CONFLICT":
                raise
        existing = await self.store.get(record_id)
        if existing is None:
            raise OpsMeshError("CONFLICT", "Concurrent delivery record changed", retryable=True)
        if existing.data.get("fingerprint") != data["fingerprint"]:
            raise OpsMeshError(
                "IDEMPOTENCY_CONFLICT", "Idempotency key was reused for different data"
            )
        if existing.data.get("state") == "retryable_failed":
            return await self.store.update(record_id, data, existing.revision), True
        return existing, False
