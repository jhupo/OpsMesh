"""Persistent bot inbox implementation supplied by the composition root."""

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select

from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.bots.conversations.api import QueuedConversationMessage

from .batches import ConversationBatchRepository
from .state import ConversationCoordinationState
from .storage import ConversationInboxStorage, channel_key, message_record, query_limit, timestamp


class PersistentConversationInbox(ConversationBatchRepository):
    def __init__(
        self,
        storage: ConversationInboxStorage,
        namespace: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(storage, namespace, clock=clock)
        self.coordination = ConversationCoordinationState(storage.state, self.scope)

    async def enqueue(self, message: QueuedConversationMessage) -> bool:
        m, now = self.messages.c, timestamp(self._clock())
        async with self.write() as connection:
            statement = self.storage.insert(self.messages).values(
                scope=self.scope,
                event_id=message.event_id,
                event_key=hashlib.sha256(message.event_id.encode()).hexdigest(),
                channel_key=channel_key(message.bot_id, message.channel_id),
                bot_id=message.bot_id,
                channel_id=message.channel_id,
                user_id=message.user_id,
                message_id=message.message_id,
                text=message.text,
                occurred_at=timestamp(message.occurred_at),
                received_at=now,
                to_me=message.to_me,
                has_attachments=message.has_attachments,
                moderation_state="pending",
                chat_eligible=False,
            )
            inserted = (
                await connection.execute(
                    statement.on_conflict_do_nothing(
                        index_elements=[m.scope, m.event_key]
                    ).returning(m.sequence)
                )
            ).scalar_one_or_none()
            if inserted is not None:
                await self.touch(
                    connection, message.bot_id, message.channel_id, "last_activity", now
                )
            return inserted is not None

    async def has_message(self, event_id: str) -> bool:
        m = self.messages.c
        async with self.read() as connection:
            return (
                await connection.execute(
                    select(m.sequence).where(m.scope == self.scope, m.event_id == event_id)
                )
            ).scalar_one_or_none() is not None

    async def pending_moderation(
        self, limit: int = 64, *, has_attachments: bool | None = None
    ) -> tuple[QueuedConversationMessage, ...]:
        query_limit(limit, 1024)
        m = self.messages.c
        statement = select(self.messages).where(
            m.scope == self.scope, m.moderation_state == "pending"
        )
        if has_attachments is not None:
            statement = statement.where(m.has_attachments.is_(has_attachments))
        async with self.read() as connection:
            rows = await connection.execute(statement.order_by(m.sequence).limit(limit))
            return tuple(message_record(row) for row in rows.mappings())

    async def set_moderation(
        self, event_id: str, decision: dict[str, Any], eligible_for_chat: bool
    ) -> None:
        import json

        if type(eligible_for_chat) is not bool or set(decision) - {
            "status",
            "action",
            "reason",
            "reason_code",
            "categories",
            "probabilities",
        }:
            raise ValueError("Only moderation result fields and a boolean eligibility are accepted")
        if decision.get("status") not in {"ok", "unknown"} or decision.get("action") not in {
            "allow",
            "remove",
            "review",
        }:
            raise ValueError("A structured moderation decision is required")
        encoded = json.dumps(decision, allow_nan=False)
        if len(encoded.encode()) > 32 * 1024:
            raise ValueError("Moderation decision exceeds the supported size")
        normalized = json.loads(encoded)
        allowed = decision["status"] == "ok" and decision["action"] == "allow"
        blocked = decision["status"] == "ok" and decision["action"] == "remove"
        state, eligible = (
            "allowed" if allowed else "blocked" if blocked else "review",
            allowed and eligible_for_chat,
        )
        m = self.messages.c
        async with self.write() as connection:
            row = (
                (
                    await connection.execute(
                        select(self.messages).where(m.scope == self.scope, m.event_id == event_id)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KeyError("Queued event does not exist")
            if row["moderation_state"] != "pending":
                if row["moderation"] != normalized or row["chat_eligible"] != eligible:
                    raise ValueError("An event's moderation decision is already finalized")
                return
            await connection.execute(
                self.messages.update()
                .where(m.scope == self.scope, m.event_id == event_id)
                .values(
                    moderation_state=state,
                    moderation=normalized,
                    moderated_at=timestamp(self._clock()),
                    chat_eligible=eligible,
                )
            )

    async def recent_messages(
        self, bot_id: str, channel_id: str, *, limit: int = 24
    ) -> tuple[QueuedConversationMessage, ...]:
        query_limit(limit, 128)
        m = self.messages.c
        async with self.read() as connection:
            rows = (
                (
                    await connection.execute(
                        select(self.messages)
                        .where(
                            m.scope == self.scope,
                            m.bot_id == bot_id,
                            m.channel_id == channel_id,
                            m.moderation_state == "allowed",
                        )
                        .order_by(m.sequence.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            return tuple(message_record(row) for row in reversed(rows))

    async def known_channels(self) -> tuple[tuple[str, str], ...]:
        a = self.activity.c
        async with self.read() as connection:
            rows = await connection.execute(
                select(a.bot_id, a.channel_id)
                .where(a.scope == self.scope)
                .order_by(a.bot_id, a.channel_id)
            )
            return tuple((row.bot_id, row.channel_id) for row in rows)

    async def last_activity(self, bot_id: str, channel_id: str) -> datetime | None:
        return await self._activity(bot_id, channel_id, "last_activity")

    async def last_reply(self, bot_id: str, channel_id: str) -> datetime | None:
        return await self._activity(bot_id, channel_id, "last_reply")

    async def _activity(self, bot_id: str, channel_id: str, field: str) -> datetime | None:
        a = self.activity.c
        async with self.read() as connection:
            value = await connection.scalar(
                select(a[field]).where(
                    a.scope == self.scope, a.bot_id == bot_id, a.channel_id == channel_id
                )
            )
            return datetime.fromisoformat(value) if value is not None else None

    async def record_reply(self, bot_id: str, channel_id: str) -> None:
        async with self.write() as connection:
            await self.touch(connection, bot_id, channel_id, "last_reply", timestamp(self._clock()))

    async def get_value(self, key: str) -> dict[str, Any] | None:
        return await self.coordination.get_value(key)

    async def set_value(self, key: str, value: dict[str, Any]) -> None:
        await self.coordination.set_value(key, value)

    async def list_values(
        self, prefix: str, *, limit: int = 128, after: str | None = None
    ) -> tuple[tuple[str, dict[str, Any]], ...]:
        return await self.coordination.list_values(prefix, limit=limit, after=after)

    async def delete_value(self, key: str) -> None:
        await self.coordination.delete_value(key)


class PersistentConversationInboxProvider:
    def __init__(self, database: AsyncDatabase) -> None:
        self.storage = ConversationInboxStorage(database)

    def open(self, namespace: str) -> PersistentConversationInbox:
        return PersistentConversationInbox(self.storage, namespace)
