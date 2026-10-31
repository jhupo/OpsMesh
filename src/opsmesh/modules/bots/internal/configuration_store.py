"""Fence Bot configuration and identity changes against the authorized snapshots."""

from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.infrastructure.database.transactions import serialized_write
from opsmesh.modules.bots.api import BotAccount, BotIdentity, BotRoute

from .record_keys import bot_record_key

BotRelatedSnapshot = dict[str, tuple[int, str]]


class BotConfigurationStore:
    def __init__(self, records: VersionedRecordStore) -> None:
        self.records = records

    async def _lock_account(
        self, connection: AsyncConnection, bot_id: str, expected_revision: int
    ) -> None:
        table = self.records.table
        revision = (
            await connection.execute(
                select(table.c.revision)
                .where(table.c.id == bot_record_key("account", bot_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if revision != expected_revision:
            raise OpsMeshError("CONFLICT", "Bot configuration changed during authorization")

    async def route_snapshot(self, bot_id: str) -> BotRelatedSnapshot:
        return {
            record.id: (record.revision, record.data["route"]["agent_revision"])
            for record in await self.records.list()
            if record.data.get("kind") == "route" and record.data["route"]["bot_id"] == bot_id
        }

    async def _check_related_snapshot(
        self, connection: AsyncConnection, bot_id: str, kind: str, expected: BotRelatedSnapshot
    ) -> None:
        target = "principal_id" if kind == "identity" else "agent_revision"
        current = {
            row.id: (row.revision, row.data[kind][target])
            for row in (await connection.execute(select(self.records.table))).mappings()
            if row.data.get("kind") == kind and row.data[kind]["bot_id"] == bot_id
        }
        if current != expected:
            raise OpsMeshError("CONFLICT", f"Bot {kind} records changed during authorization")

    async def _save_related_record(
        self,
        connection: AsyncConnection,
        identifier: str,
        data: JsonData,
        previous: StoredRecord | None,
    ) -> None:
        table = self.records.table
        current = (
            (
                await connection.execute(
                    select(table).where(table.c.id == identifier).with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if (
            (previous is None and current is not None)
            or (previous is not None and current is None)
            or (
                previous is not None
                and current is not None
                and (current.revision != previous.revision or current.data != previous.data)
            )
        ):
            raise OpsMeshError("CONFLICT", "Bot record changed during authorization")
        now = datetime.now(UTC)
        if previous is None:
            await connection.execute(
                insert(table).values(
                    id=identifier, revision=1, data=data, created_at=now, updated_at=now
                )
            )
        else:
            result = await connection.execute(
                update(table)
                .where(table.c.id == identifier, table.c.revision == previous.revision)
                .values(data=data, revision=previous.revision + 1, updated_at=now)
            )
            if result.rowcount != 1:
                raise OpsMeshError("CONFLICT", "Bot record changed during authorization")

    async def _delete_related_record(
        self, connection: AsyncConnection, previous: StoredRecord
    ) -> None:
        table = self.records.table
        current = (
            (
                await connection.execute(
                    select(table).where(table.c.id == previous.id).with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if (
            current is None
            or current.revision != previous.revision
            or current.data != previous.data
        ):
            raise OpsMeshError("CONFLICT", "Bot record changed during authorization")
        result = await connection.execute(
            delete(table).where(table.c.id == previous.id, table.c.revision == previous.revision)
        )
        if result.rowcount != 1:
            raise OpsMeshError("CONFLICT", "Bot record changed during authorization")

    async def replace_account(
        self,
        account: BotAccount,
        expected_revision: int,
        identity_snapshot: BotRelatedSnapshot,
        events: list[JsonData],
    ) -> StoredRecord:
        table = self.records.table
        identifier = bot_record_key("account", account.id)
        async with serialized_write(self.records.database.engine) as connection:
            await self._lock_account(connection, account.id, expected_revision)
            await self._check_related_snapshot(
                connection, account.id, "identity", identity_snapshot
            )
            await connection.execute(
                update(table)
                .where(table.c.id == identifier, table.c.revision == expected_revision)
                .values(
                    data={"kind": "account", "account": account.model_dump(mode="json")},
                    revision=expected_revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            await self.records.database.enqueue_events(connection, events)
            saved = (
                (await connection.execute(select(table).where(table.c.id == identifier)))
                .mappings()
                .one()
            )
            return self.records._record(saved)

    async def save_identity(
        self,
        identity: BotIdentity,
        account_revision: int,
        previous: StoredRecord | None,
        events: list[JsonData],
        route_snapshot: BotRelatedSnapshot,
    ) -> None:
        identifier = bot_record_key("identity", identity.bot_id, identity.external_user_id)
        data = {"kind": "identity", "identity": identity.model_dump(mode="json")}
        try:
            async with serialized_write(self.records.database.engine) as connection:
                await self._lock_account(connection, identity.bot_id, account_revision)
                await self._check_related_snapshot(
                    connection, identity.bot_id, "route", route_snapshot
                )
                await self._save_related_record(connection, identifier, data, previous)
                await self.records.database.enqueue_events(connection, events)
        except IntegrityError as error:
            raise OpsMeshError("CONFLICT", "Bot identity changed during authorization") from error

    async def save_route(
        self,
        route: BotRoute,
        account_revision: int,
        previous: StoredRecord | None,
        identity_snapshot: BotRelatedSnapshot,
        events: list[JsonData],
    ) -> None:
        identifier = bot_record_key("route", route.bot_id, route.channel_id)
        data = {"kind": "route", "route": route.model_dump(mode="json")}
        try:
            async with serialized_write(self.records.database.engine) as connection:
                await self._lock_account(connection, route.bot_id, account_revision)
                await self._check_related_snapshot(
                    connection, route.bot_id, "identity", identity_snapshot
                )
                await self._save_related_record(connection, identifier, data, previous)
                await self.records.database.enqueue_events(connection, events)
        except IntegrityError as error:
            raise OpsMeshError("CONFLICT", "Bot route changed during authorization") from error

    async def delete_identity(
        self,
        bot_id: str,
        account_revision: int,
        previous: StoredRecord,
        events: list[JsonData],
    ) -> None:
        async with serialized_write(self.records.database.engine) as connection:
            await self._lock_account(connection, bot_id, account_revision)
            await self._delete_related_record(connection, previous)
            await self.records.database.enqueue_events(connection, events)

    async def delete_route(
        self,
        bot_id: str,
        account_revision: int,
        previous: StoredRecord,
        identity_snapshot: BotRelatedSnapshot,
        events: list[JsonData],
    ) -> None:
        async with serialized_write(self.records.database.engine) as connection:
            await self._lock_account(connection, bot_id, account_revision)
            await self._check_related_snapshot(connection, bot_id, "identity", identity_snapshot)
            await self._delete_related_record(connection, previous)
            await self.records.database.enqueue_events(connection, events)
