"""CAS-protected model history; independent of display-message history."""

from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore


class ModelHistory:
    def __init__(self, store: VersionedRecordStore, session_id: str) -> None:
        self.store = store
        self.session_id = session_id
        self._read_revision: int | None = None

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        record = await self.store.get(self.session_id)
        self._read_revision = record.revision if record else None
        items: list[dict[str, Any]] = record.data["items"] if record else []
        return items if limit is None else (items[-limit:] if limit else [])

    async def _atomic_replace_items(self, items: list[dict[str, Any]]) -> bool:
        """Pinned SDK compaction extension; the last history read is its CAS boundary."""
        if self._read_revision is None:
            raise OpsMeshError("CONFLICT", "Compaction requires an existing history snapshot")
        record = await self.store.update(self.session_id, {"items": items}, self._read_revision)
        self._read_revision = record.revision
        return True

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        for _ in range(5):
            record = await self.store.get(self.session_id)
            try:
                if record is None:
                    await self.store.create(self.session_id, {"items": items})
                else:
                    await self.store.update(
                        self.session_id, {"items": record.data["items"] + items}, record.revision
                    )
                return
            except OpsMeshError as exc:
                if exc.code != "CONFLICT":
                    raise
        raise OpsMeshError("conflict", "Concurrent model history modification")

    async def pop_item(self) -> dict[str, Any] | None:
        record = await self.store.get(self.session_id)
        if record is None or not record.data["items"]:
            return None
        items: list[dict[str, Any]] = record.data["items"]
        await self.store.update(self.session_id, {"items": items[:-1]}, record.revision)
        return items[-1]

    async def clear_session(self) -> None:
        record = await self.store.get(self.session_id)
        if record:
            await self.store.update(self.session_id, {"items": []}, record.revision)
