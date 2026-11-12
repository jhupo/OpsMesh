"""Own conversation indexes and borrow native SDK sessions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agents.extensions.memory import SQLAlchemySession
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_object_session,
    async_sessionmaker,
)

from .schemas import ConversationView


class ConversationError(RuntimeError):
    """An operation cannot access or mutate the requested conversation."""

    def __init__(self, status: int, message: str) -> None:
        """Store a safe boundary status and message."""
        super().__init__(message)
        self.status = status


class ConversationStore:
    """Store metadata while SDK sessions own all history reads and writes.

    PostgreSQL row locks protect across hosts. The local guard also rejects
    reentrant calls immediately and supports isolated SQLite test databases.
    """

    record_type: type
    sessions_table: str
    messages_table: str

    def __init__(self, engine: AsyncEngine) -> None:
        """Borrow the database plugin engine without owning its lifetime."""
        self.engine = engine
        self._database = async_sessionmaker(engine, expire_on_commit=False)
        self._active: set[str] = set()

    def history(self, session_id: str) -> SQLAlchemySession:
        """Construct a native history handle without opening a new pool."""
        return SQLAlchemySession(
            session_id,
            engine=self.engine,
            sessions_table=self.sessions_table,
            messages_table=self.messages_table,
        )

    async def create(
        self,
        owner_id: str,
        name: str = "New conversation",
        *,
        session_id: str | None = None,
    ) -> ConversationView:
        """Persist an empty conversation before returning its identifier."""
        row = self.record_type(
            id=session_id or str(uuid4()), owner_id=owner_id, name=name
        )
        try:
            async with self._database.begin() as database:
                database.add(row)
                await database.flush()
                view = ConversationView.model_validate(row)
            return view
        except IntegrityError:
            if session_id is None:
                raise
            return await self.get(owner_id, session_id)

    async def get(self, owner_id: str, session_id: str) -> ConversationView:
        """Read an owned index without exposing other users' existence."""
        async with self._database() as database:
            row = await database.scalar(
                select(self.record_type).where(
                    self.record_type.id == session_id,
                    self.record_type.owner_id == owner_id,
                )
            )
            if row is None:
                raise ConversationError(404, "Conversation not found")
            return ConversationView.model_validate(row)

    async def list(
        self,
        owner_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationView]:
        """List owned indexes with bounded, deterministic pagination."""
        if not 1 <= limit <= 200 or offset < 0:
            raise ConversationError(422, "Invalid pagination")
        async with self._database() as database:
            rows = await database.scalars(
                select(self.record_type)
                .where(
                    self.record_type.owner_id == owner_id,
                )
                .order_by(
                    self.record_type.updated_at.desc(), self.record_type.id
                )
                .limit(limit)
                .offset(offset)
            )
            return [ConversationView.model_validate(row) for row in rows]

    @asynccontextmanager
    async def hold(
        self,
        owner_id: str,
        session_id: str,
        *,
        deleting: bool = False,
    ) -> AsyncIterator[Any]:
        """Exclude execution, reads, clear and delete for the same history.

        The row lock lasts until SDK work and cleanup finish. SDK history uses
        separate short transactions, so it never waits for its own index lock.
        """
        await self.get(owner_id, session_id)
        if session_id in self._active:
            raise ConversationError(409, "Conversation is busy")
        self._active.add(session_id)
        try:
            async with self._database.begin() as database:
                try:
                    row = await database.scalar(
                        select(self.record_type)
                        .where(
                            self.record_type.id == session_id,
                            self.record_type.owner_id == owner_id,
                        )
                        .with_for_update(nowait=True)
                    )
                except DBAPIError as error:
                    if getattr(error.orig, "sqlstate", None) == "55P03":
                        raise ConversationError(
                            409, "Conversation is busy"
                        ) from None
                    raise
                if row is None:
                    raise ConversationError(404, "Conversation not found")
                if row.deleting and not deleting:
                    raise ConversationError(
                        409, "Conversation is being deleted"
                    )
                yield row
                row.updated_at = datetime.now(UTC)
        finally:
            self._active.remove(session_id)

    async def clear(self, owner_id: str, session_id: str) -> None:
        """Clear native history while retaining the conversation index."""
        async with self.hold(owner_id, session_id):
            await self.history(session_id).clear_session()

    async def messages(
        self,
        owner_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[dict]:
        """Return a bounded native history snapshot after ownership checks."""
        if not 1 <= limit <= 200:
            raise ConversationError(422, "Invalid history limit")
        async with self.hold(owner_id, session_id):
            return await self.history(session_id).get_items(limit=limit)

    async def delete(self, owner_id: str, session_id: str) -> None:
        """Persist deletion intent, clear SDK history, then remove the index.

        If cleanup fails, a later DELETE retries the marked conversation;
        normal execution remains blocked until deletion completes.
        """
        async with self.hold(owner_id, session_id, deleting=True) as row:
            row.deleting = True
        async with self.hold(owner_id, session_id, deleting=True) as row:
            await self.history(session_id).clear_session()
            await self.remove_assets(session_id)
            database = async_object_session(row)
            if database is None:
                raise RuntimeError("Conversation lock lost its transaction")
            await database.delete(row)

    async def remove_assets(self, session_id: str) -> None:
        """Release optional plugin-owned assets before removing an index."""
