"""Atomic batch reservations, run identities, completion and recovery."""

import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from opsmesh.modules.bots.conversations.api import (
    ATTACHMENT_TEXT_LIMIT,
    ConversationBatch,
    TerminalBatchState,
)

from .storage import (
    ACTIVE,
    FINAL,
    ScopedInboxStorage,
    channel_key,
    identifier,
    message_record,
    query_limit,
    timestamp,
)


class ConversationBatchRepository(ScopedInboxStorage):
    async def ready_channels(self) -> tuple[tuple[str, str], ...]:
        m, b = self.messages.c, self.batches.c
        query = select(m.bot_id, m.channel_id).where(
            m.scope == self.scope,
            m.chat_eligible.is_(True),
            m.batch_id.is_(None),
            ~exists().where(
                b.scope == m.scope,
                b.bot_id == m.bot_id,
                b.channel_id == m.channel_id,
                b.state.in_(ACTIVE),
            ),
        )
        async with self.read() as connection:
            rows = await connection.execute(
                query.group_by(m.bot_id, m.channel_id).order_by(func.min(m.sequence))
            )
            return tuple((row.bot_id, row.channel_id) for row in rows)

    async def claim_batch(
        self, bot_id: str, channel_id: str, *, max_chars: int = 12_000
    ) -> ConversationBatch | None:
        identifier(bot_id)
        identifier(channel_id)
        query_limit(max_chars, 64_000)
        m, b = self.messages.c, self.batches.c
        async with self.write() as connection:
            current = await connection.scalar(
                select(b.batch_id).where(
                    b.scope == self.scope,
                    b.bot_id == bot_id,
                    b.channel_id == channel_id,
                    b.state.in_(ACTIVE),
                )
            )
            if current is not None:
                return None
            rows = (
                await connection.stream(
                    select(self.messages)
                    .where(
                        m.scope == self.scope,
                        m.bot_id == bot_id,
                        m.channel_id == channel_id,
                        m.chat_eligible.is_(True),
                        m.batch_id.is_(None),
                    )
                    .order_by(m.sequence)
                )
            ).mappings()
            selected: list[RowMapping] = []
            size = 0
            async for row in rows:
                # Reserve the event envelope and bounded attachment description as well as text.
                length = len(row["text"]) + len(row["event_id"]) + 160
                if row["has_attachments"]:
                    length += ATTACHMENT_TEXT_LIMIT
                if selected and size + length > max_chars:
                    break
                selected.append(row)
                size += length
                if size >= max_chars:
                    break
            if not selected:
                return None
            await rows.close()
            batch_id, now = "conversation-" + uuid4().hex, timestamp(self._clock())
            await connection.execute(
                self.batches.insert().values(
                    scope=self.scope,
                    batch_id=batch_id,
                    channel_key=channel_key(bot_id, channel_id),
                    bot_id=bot_id,
                    channel_id=channel_id,
                    state="claimed",
                    oversized=size > max_chars,
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                self.messages.update()
                .where(
                    m.scope == self.scope,
                    m.sequence.in_([row["sequence"] for row in selected]),
                    m.batch_id.is_(None),
                )
                .values(batch_id=batch_id)
            )
            return await self._batch(connection, batch_id)

    async def _batch(self, connection: AsyncConnection, batch_id: str) -> ConversationBatch:
        b, m = self.batches.c, self.messages.c
        row = (
            (
                await connection.execute(
                    select(self.batches).where(b.scope == self.scope, b.batch_id == batch_id)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise KeyError("Conversation batch does not exist")
        messages = (
            await connection.execute(
                select(self.messages)
                .where(m.scope == self.scope, m.batch_id == batch_id)
                .order_by(m.sequence)
            )
        ).mappings()
        return ConversationBatch(
            batch_id=row["batch_id"],
            bot_id=row["bot_id"],
            channel_id=row["channel_id"],
            messages=tuple(message_record(item) for item in messages),
            state=row["state"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            run_id=row["run_id"],
            delivery_id=row["delivery_id"],
            error_code=row["error_code"],
            oversized=row["oversized"],
        )

    async def get_batch(self, batch_id: str) -> ConversationBatch:
        async with self.read() as connection:
            return await self._batch(connection, batch_id)

    async def pending_batches(
        self, *, include_failed: bool = False
    ) -> tuple[ConversationBatch, ...]:
        b = self.batches.c
        async with self.read() as connection:
            rows = await connection.execute(
                select(b.batch_id)
                .where(
                    b.scope == self.scope,
                    b.state.in_((*ACTIVE, "failed") if include_failed else ACTIVE),
                )
                .order_by(b.created_at, b.batch_id)
            )
            return tuple([await self._batch(connection, row.batch_id) for row in rows])

    async def record_run(self, batch_id: str, run_id: str) -> None:
        identifier(run_id)
        async with self.write() as connection:
            batch = await self._batch(connection, batch_id)
            if batch.run_id is not None:
                if batch.run_id != run_id:
                    raise ValueError("A batch cannot be attached to two different runs")
                return
            if batch.state not in ACTIVE:
                raise ValueError("Only an active batch can start a run")
            await connection.execute(
                self.batches.update()
                .where(self.batches.c.scope == self.scope, self.batches.c.batch_id == batch_id)
                .values(run_id=run_id, state="running", updated_at=timestamp(self._clock()))
            )

    async def complete_batch(
        self,
        batch_id: str,
        *,
        run_id: str | None = None,
        delivery_id: str | None = None,
        state: TerminalBatchState = "replied",
    ) -> None:
        if state not in FINAL:
            raise ValueError("Unsupported completed batch state")
        for value in (run_id, delivery_id):
            if value is not None:
                identifier(value)
        async with self.write() as connection:
            batch = await self._batch(connection, batch_id)
            if batch.run_id is not None and run_id is not None and batch.run_id != run_id:
                raise ValueError("Completion belongs to a different run")
            if batch.state in FINAL:
                if batch.state != state or (
                    delivery_id is not None and batch.delivery_id != delivery_id
                ):
                    raise ValueError("Batch completion is already finalized")
                return
            now = timestamp(self._clock())
            await connection.execute(
                self.batches.update()
                .where(self.batches.c.scope == self.scope, self.batches.c.batch_id == batch_id)
                .values(
                    state=state,
                    run_id=run_id or batch.run_id,
                    delivery_id=delivery_id or batch.delivery_id,
                    updated_at=now,
                    completed_at=now,
                )
            )
            if state == "replied":
                await self.touch(connection, batch.bot_id, batch.channel_id, "last_reply", now)

    async def record_error(self, batch_id: str, error_code: str) -> None:
        await self._set_error(batch_id, error_code, terminal=False)

    async def mark_failed(self, batch_id: str, error_code: str) -> None:
        await self._set_error(batch_id, error_code, terminal=True)

    async def _set_error(self, batch_id: str, error_code: str, *, terminal: bool) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", error_code):
            raise ValueError("Use a short machine error code, not an exception body")
        async with self.write() as connection:
            batch = await self._batch(connection, batch_id)
            if batch.state in FINAL:
                raise ValueError("A completed batch cannot be changed to an error")
            now = timestamp(self._clock())
            await connection.execute(
                self.batches.update()
                .where(self.batches.c.scope == self.scope, self.batches.c.batch_id == batch_id)
                .values(
                    error_code=error_code,
                    state="failed" if terminal else batch.state,
                    updated_at=now,
                    completed_at=now if terminal or batch.state == "failed" else None,
                )
            )

    async def purge_before(self, cutoff: datetime) -> int:
        boundary = timestamp(cutoff)
        m, b = self.messages.c, self.batches.c
        async with self.write() as connection:
            completed = select(b.batch_id).where(
                b.scope == self.scope, b.state.in_(FINAL), b.completed_at < boundary
            )
            result = await connection.execute(
                delete(self.messages).where(
                    m.scope == self.scope,
                    m.batch_id.in_(completed)
                    | and_(
                        m.batch_id.is_(None),
                        m.moderation_state != "pending",
                        m.chat_eligible.is_(False),
                        m.moderated_at < boundary,
                    ),
                )
            )
            await connection.execute(
                delete(self.batches).where(
                    b.scope == self.scope,
                    b.state.in_(FINAL),
                    b.completed_at < boundary,
                    ~exists().where(m.batch_id == b.batch_id),
                )
            )
            return result.rowcount
