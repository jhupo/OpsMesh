"""Async database lifecycle and stable persistence entry points."""

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import MetaData, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.database.migrations import (
    migrate_schema,
    schema_versions_table,
    verify_schema,
)


class AsyncDatabase:
    def __init__(self, url: str) -> None:
        # Delay the composed outbox dependency so both packages import independently.
        from opsmesh.infrastructure.messaging.outbox import TransactionalOutbox

        try:
            parsed = make_url(url)
        except ArgumentError as error:
            raise OpsMeshError("CONFIG_INVALID", "Database URL is invalid") from error
        if parsed.drivername in {"postgres", "postgresql"}:
            parsed = parsed.set(drivername="postgresql+asyncpg")
        elif parsed.drivername == "sqlite":
            parsed = parsed.set(drivername="sqlite+aiosqlite")
        if parsed.drivername not in {"postgresql+asyncpg", "sqlite+aiosqlite"}:
            raise OpsMeshError(
                "FEATURE_UNSUPPORTED", "Database driver must be async PostgreSQL or SQLite"
            )
        self.engine = create_async_engine(parsed, pool_pre_ping=True)
        self.metadata = MetaData()
        self._outbox = TransactionalOutbox(self.engine, self.metadata)
        self.outbox = self._outbox.table
        self._versions = schema_versions_table(self.metadata)
        self._verified: set[str] = set()

    async def initialize(self) -> None:
        """Execute explicit versioned schema migrations; assemble stores before calling."""
        self._verified.clear()
        self._verified = await migrate_schema(self.engine, self.metadata, self._versions)

    async def verify_schema(self) -> None:
        """Check schema without creating or modifying any application table."""
        self._verified.clear()
        self._verified = await verify_schema(self.engine, self.metadata, self._versions)

    def require_table(self, name: str) -> None:
        if name not in self._verified:
            raise OpsMeshError(
                "SCHEMA_NOT_INITIALIZED", f"Table {name} requires migration and verification"
            )

    async def close(self) -> None:
        await self.engine.dispose()
        self._verified.clear()

    async def referenced_content_digests(self) -> set[str]:
        """Conservative maintenance scan across all registered module and event JSON data."""
        references: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                references.add(value)
            elif isinstance(value, dict):
                for key, nested in value.items():
                    visit(key)
                    visit(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested)

        async with self.engine.connect() as connection:
            for table in self.metadata.sorted_tables:
                column = table.c.get("data") if "data" in table.c else table.c.get("event")
                if column is None:
                    continue
                self.require_table(table.name)
                rows = await connection.stream(select(column))
                async for row in rows:
                    visit(row[0])
        return references

    async def enqueue_events(self, connection: AsyncConnection, events: Sequence[JsonData]) -> None:
        self.require_table(self.outbox.name)
        await self._outbox.enqueue(connection, events)

    async def pending_events(self, limit: int = 100) -> list[JsonData]:
        self.require_table(self.outbox.name)
        return await self._outbox.pending(limit)

    async def claim_events(
        self, worker_id: str, limit: int = 100, lease_seconds: float = 30, max_attempts: int = 5
    ) -> list[JsonData]:
        self.require_table(self.outbox.name)
        return await self._outbox.claim(worker_id, limit, lease_seconds, max_attempts)

    async def acknowledge_event(self, event_id: str, owner: str | None = None) -> None:
        self.require_table(self.outbox.name)
        await self._outbox.acknowledge(event_id, owner)

    async def reject_event(
        self,
        event_id: str,
        owner: str | None = None,
        *,
        max_attempts: int = 5,
        error: str = "Delivery failed",
        retry_base_seconds: float = 1,
    ) -> None:
        self.require_table(self.outbox.name)
        await self._outbox.reject(
            event_id,
            owner,
            max_attempts=max_attempts,
            error=error,
            retry_base_seconds=retry_base_seconds,
        )

    async def renew_event_lease(self, event_id: str, owner: str, lease_seconds: float = 30) -> None:
        self.require_table(self.outbox.name)
        await self._outbox.renew(event_id, owner, lease_seconds)

    async def dead_letters(self, limit: int = 100) -> list[JsonData]:
        self.require_table(self.outbox.name)
        return await self._outbox.dead_letters(limit)

    async def requeue_event(self, event_id: str) -> None:
        self.require_table(self.outbox.name)
        await self._outbox.requeue(event_id)

    async def inspect_events(
        self, *, status: str = "dead", limit: int = 100, offset: int = 0
    ) -> list[JsonData]:
        self.require_table(self.outbox.name)
        return await self._outbox.inspect(status=status, limit=limit, offset=offset)
