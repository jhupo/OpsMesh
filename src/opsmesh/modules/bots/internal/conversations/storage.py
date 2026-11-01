"""Bot-owned inbox tables on the platform database and transaction lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.transactions import database_connection, serialized_write
from opsmesh.modules.bots.conversations.api import QueuedConversationMessage

ACTIVE = ("claimed", "running")
FINAL = ("replied", "observed", "review")


def timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Inbox timestamps must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Inbox identifiers must contain 1 to 2048 characters")


def channel_key(bot_id: str, channel_id: str) -> str:
    return hashlib.sha256(f"{len(bot_id)}:{bot_id}{channel_id}".encode()).hexdigest()


def query_limit(value: int, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("Inbox query limit is outside its supported range")


def message_record(row: RowMapping) -> QueuedConversationMessage:
    return QueuedConversationMessage(
        **{
            key: row[key]
            for key in (
                "event_id",
                "bot_id",
                "channel_id",
                "user_id",
                "message_id",
                "text",
                "to_me",
                "has_attachments",
            )
        },
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
    )


class ConversationInboxStorage:
    def __init__(self, database: AsyncDatabase) -> None:
        self.database = database
        self.state = VersionedRecordStore(database, "bots_conversation_state")
        metadata = database.metadata
        if "bots_conversation_scopes" in metadata.tables:
            self.scopes = metadata.tables["bots_conversation_scopes"]
            self.messages = metadata.tables["bots_conversation_messages"]
            self.batches = metadata.tables["bots_conversation_batches"]
            self.activity = metadata.tables["bots_conversation_activity"]
            return
        self.scopes = Table(
            "bots_conversation_scopes", metadata, Column("scope", String(64), primary_key=True)
        )
        self.batches = Table(
            "bots_conversation_batches",
            metadata,
            Column("scope", String(64), nullable=False),
            Column("batch_id", String(64), primary_key=True),
            Column("channel_key", String(64), nullable=False),
            Column("bot_id", String(2048), nullable=False),
            Column("channel_id", String(2048), nullable=False),
            Column("state", String(16), nullable=False),
            Column("run_id", String(2048)),
            Column("delivery_id", String(2048)),
            Column("error_code", String(120)),
            Column("oversized", Boolean, nullable=False),
            Column("created_at", String(40), nullable=False),
            Column("updated_at", String(40), nullable=False),
            Column("completed_at", String(40)),
        )
        Index(
            "ix_bots_conversation_active",
            self.batches.c.scope,
            self.batches.c.channel_key,
            unique=True,
            sqlite_where=self.batches.c.state.in_(ACTIVE),
            postgresql_where=self.batches.c.state.in_(ACTIVE),
        )
        self.messages = Table(
            "bots_conversation_messages",
            metadata,
            Column(
                "sequence",
                BigInteger().with_variant(Integer, "sqlite"),
                primary_key=True,
                autoincrement=True,
            ),
            Column("scope", String(64), nullable=False),
            Column("event_key", String(64), nullable=False),
            Column("channel_key", String(64), nullable=False),
            *(
                Column(name, String(2048), nullable=False)
                for name in ("event_id", "bot_id", "channel_id", "user_id", "message_id")
            ),
            Column("text", Text, nullable=False),
            Column("occurred_at", String(40), nullable=False),
            Column("received_at", String(40), nullable=False),
            Column("to_me", Boolean, nullable=False),
            Column("has_attachments", Boolean, nullable=False),
            Column("moderation_state", String(16), nullable=False),
            Column("moderation", JSON),
            Column("moderated_at", String(40)),
            Column("chat_eligible", Boolean, nullable=False),
            Column("batch_id", String(64), ForeignKey(self.batches.c.batch_id)),
        )
        Index(
            "ix_bots_conversation_event",
            self.messages.c.scope,
            self.messages.c.event_key,
            unique=True,
        )
        Index(
            "ix_bots_conversation_pending",
            self.messages.c.scope,
            self.messages.c.moderation_state,
            self.messages.c.sequence,
        )
        Index(
            "ix_bots_conversation_channel",
            self.messages.c.scope,
            self.messages.c.channel_key,
            self.messages.c.batch_id,
            self.messages.c.sequence,
        )
        self.activity = Table(
            "bots_conversation_activity",
            metadata,
            Column("scope", String(64), primary_key=True),
            Column("channel_key", String(64), primary_key=True),
            Column("bot_id", String(2048), nullable=False),
            Column("channel_id", String(2048), nullable=False),
            Column("last_activity", String(40)),
            Column("last_reply", String(40)),
        )

    def ready(self) -> None:
        for table in (self.scopes, self.messages, self.batches, self.activity):
            self.database.require_table(table.name)

    def insert(self, table: Table) -> Any:
        return (sqlite_insert if self.database.engine.dialect.name == "sqlite" else pg_insert)(
            table
        )


class ScopedInboxStorage:
    def __init__(
        self,
        storage: ConversationInboxStorage,
        namespace: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        identifier(namespace)
        self.storage = storage
        self.scope = hashlib.sha256(namespace.encode()).hexdigest()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.messages, self.batches, self.activity = (
            storage.messages,
            storage.batches,
            storage.activity,
        )

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncConnection]:
        self.storage.ready()
        async with database_connection(self.storage.database.engine) as connection:
            yield connection

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncConnection]:
        self.storage.ready()
        async with serialized_write(self.storage.database.engine) as connection:
            table = self.storage.scopes
            await connection.execute(
                self.storage.insert(table)
                .values(scope=self.scope)
                .on_conflict_do_nothing(index_elements=[table.c.scope])
            )
            # PostgreSQL serializes transitions in this scope; SQLite uses the shared write lock.
            await connection.execute(
                select(table.c.scope).where(table.c.scope == self.scope).with_for_update()
            )
            yield connection

    async def touch(
        self, connection: AsyncConnection, bot_id: str, channel_id: str, field: str, now: str
    ) -> None:
        table = self.activity
        statement = self.storage.insert(table).values(
            scope=self.scope,
            channel_key=channel_key(bot_id, channel_id),
            bot_id=bot_id,
            channel_id=channel_id,
            **{field: now},
        )
        await connection.execute(
            statement.on_conflict_do_nothing(index_elements=[table.c.scope, table.c.channel_key])
        )
        await connection.execute(
            table.update()
            .where(
                table.c.scope == self.scope,
                table.c.bot_id == bot_id,
                table.c.channel_id == channel_id,
                (table.c[field].is_(None)) | (table.c[field] < now),
            )
            .values(**{field: now})
        )
