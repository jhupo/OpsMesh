"""Scoped coordination metadata using the platform's versioned record store."""

import json
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore


class ConversationCoordinationState:
    def __init__(self, store: VersionedRecordStore, scope: str) -> None:
        self.store, self.prefix = store, scope + ":"

    def _key(self, key: str) -> str:
        if not isinstance(key, str) or not key or len(self.prefix + key) > 255:
            raise ValueError("Conversation state key is empty or too long")
        return self.prefix + key

    async def get_value(self, key: str) -> dict[str, Any] | None:
        record = await self.store.get(self._key(key))
        return record.data if record is not None else None

    async def set_value(self, key: str, value: dict[str, Any]) -> None:
        record_id = self._key(key)
        if not isinstance(value, dict):
            raise ValueError("Conversation state values must be JSON objects")
        _reject_credentials(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 128 * 1024:
            raise ValueError("Conversation state metadata is too large")
        copied = json.loads(encoded)
        for _ in range(8):
            record = await self.store.get(record_id)
            try:
                if record is None:
                    await self.store.create(record_id, copied)
                else:
                    await self.store.update(record_id, copied, record.revision)
                return
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
        raise OpsMeshError("CONFLICT", "Conversation coordination state is busy")

    async def list_values(
        self, prefix: str, *, limit: int = 128, after: str | None = None
    ) -> tuple[tuple[str, dict[str, Any]], ...]:
        rows = await self.store.scan(
            self._key(prefix), after=self._key(after) if after is not None else None, limit=limit
        )
        return tuple((row.id[len(self.prefix) :], row.data) for row in rows)

    async def delete_value(self, key: str) -> None:
        for _ in range(8):
            record = await self.store.get(self._key(key))
            if record is None:
                return
            try:
                await self.store.delete(record.id, record.revision)
                return
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
        raise OpsMeshError("CONFLICT", "Conversation coordination state is busy")


def _reject_credentials(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("Conversation state metadata is nested too deeply")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in {
                "api_key",
                "app_secret",
                "client_secret",
                "password",
                "access_token",
                "authorization",
                "secret",
            }:
                raise ValueError("Keep credentials outside conversation coordination state")
            _reject_credentials(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_credentials(child, depth + 1)
