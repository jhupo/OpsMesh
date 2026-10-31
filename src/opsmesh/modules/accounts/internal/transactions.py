"""Atomic account mutations and reference checks across processes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore
from opsmesh.infrastructure.database.records import validate_record_id, validated_json
from opsmesh.infrastructure.database.transactions import serialized_write


class AccountTransaction:
    def __init__(self, database: AsyncDatabase, connection: AsyncConnection) -> None:
        self.database, self.connection = database, connection

    async def get(self, store: VersionedRecordStore, key: str) -> StoredRecord:
        self.database.require_table(store.table.name)
        row = (
            (await self.connection.execute(select(store.table).where(store.table.c.id == key)))
            .mappings()
            .first()
        )
        if row is None:
            raise OpsMeshError("NOT_FOUND", "Account resource does not exist")
        return StoredRecord.model_validate(dict(row))

    async def list(self, store: VersionedRecordStore) -> list[StoredRecord]:
        self.database.require_table(store.table.name)
        rows = (await self.connection.execute(select(store.table))).mappings()
        return [StoredRecord.model_validate(dict(row)) for row in rows]

    async def write(
        self,
        store: VersionedRecordStore,
        key: str,
        data: dict[str, Any],
        *,
        actor_id: str,
        action: str,
        expected_revision: int | None = None,
    ) -> StoredRecord:
        validate_record_id(key)
        self.database.require_table(store.table.name)
        data = validated_json(data)
        now = datetime.now(UTC)
        if expected_revision is None:
            await self.connection.execute(
                insert(store.table).values(
                    id=key,
                    revision=1,
                    data=data,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            changed = await self.connection.execute(
                update(store.table)
                .where(
                    store.table.c.id == key,
                    store.table.c.revision == expected_revision,
                )
                .values(data=data, revision=expected_revision + 1, updated_at=now)
            )
            if changed.rowcount != 1:
                raise OpsMeshError("CONFLICT", "Account resource changed; reload before editing")
        await self.event(action, key, actor_id, revision=(expected_revision or 0) + 1)
        return await self.get(store, key)

    async def delete(
        self,
        store: VersionedRecordStore,
        key: str,
        *,
        actor_id: str,
        action: str,
        expected_revision: int,
    ) -> None:
        self.database.require_table(store.table.name)
        changed = await self.connection.execute(
            delete(store.table).where(
                store.table.c.id == key,
                store.table.c.revision == expected_revision,
            )
        )
        if changed.rowcount != 1:
            raise OpsMeshError("CONFLICT", "Account resource changed; reload before editing")
        await self.event(action, key, actor_id, revision=expected_revision)

    async def event(self, action: str, key: str, actor_id: str, *, revision: int) -> None:
        await self.database.enqueue_events(
            self.connection,
            [
                {
                    "type": action,
                    "source": "accounts",
                    "actor_ref": actor_id,
                    "subject": key,
                    "payload": {"revision": revision},
                }
            ],
        )


@asynccontextmanager
async def account_transaction(database: AsyncDatabase) -> AsyncIterator[AccountTransaction]:
    try:
        async with serialized_write(database.engine) as connection:
            if database.engine.dialect.name == "postgresql":
                # All protected account mutations share a domain lock. In SQLite BEGIN
                # IMMEDIATE already prevents concurrent last-admin/reference races.
                await connection.execute(text("SELECT pg_advisory_xact_lock(723091884)"))
            yield AccountTransaction(database, connection)
    except IntegrityError as error:
        raise OpsMeshError("CONFLICT", "Account resource already exists") from error
