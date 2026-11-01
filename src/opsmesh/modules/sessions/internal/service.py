"""Durable conversation ownership, messages and logical run claims."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore
from opsmesh.infrastructure.database.transactions import database_connection
from opsmesh.modules.sessions.api import ConversationMessage, ConversationSession


class ConversationService:
    def __init__(self, database: AsyncDatabase) -> None:
        self.sessions = VersionedRecordStore(database, "sessions_conversations")
        self.messages = VersionedRecordStore(database, "sessions_messages")

    @staticmethod
    def _view(row: StoredRecord) -> ConversationSession:
        return ConversationSession.model_validate({**row.data, "revision": row.revision})

    async def open(
        self, owner_id: str, agent_ref: str, workspace_id: str | None = None
    ) -> ConversationSession:
        view = ConversationSession(
            id=str(uuid4()),
            owner_id=owner_id,
            agent_ref=agent_ref,
            workspace_id=workspace_id,
            created_at=datetime.now(UTC),
        )
        return self._view(await self.sessions.create(view.id, view.model_dump(mode="json")))

    async def get(self, session_id: str) -> ConversationSession:
        row = await self.sessions.get(session_id)
        if row is None:
            raise OpsMeshError("not_found", "Conversation does not exist")
        return self._view(row)

    async def get_or_open(
        self, key: str, owner_id: str, agent_ref: str, workspace_id: str | None = None
    ) -> ConversationSession:
        identifier = str(uuid5(NAMESPACE_URL, f"opsmesh:conversation:{owner_id}:{key}"))
        existing = await self.sessions.get(identifier)
        if existing is not None:
            return self._view(existing)
        view = ConversationSession(
            id=identifier,
            owner_id=owner_id,
            agent_ref=agent_ref,
            workspace_id=workspace_id,
            created_at=datetime.now(UTC),
        )
        try:
            return self._view(await self.sessions.create(identifier, view.model_dump(mode="json")))
        except OpsMeshError as error:
            if error.code != "CONFLICT":
                raise
            return await self.get(identifier)

    async def list(self, owner_id: str | None = None) -> list[ConversationSession]:
        return [
            self._view(row)
            for row in await self.sessions.list()
            if owner_id is None or row.data["owner_id"] == owner_id
        ]

    async def claim(self, session_id: str, run_id: str) -> None:
        view = await self.get(session_id)
        if view.status != "open":
            raise OpsMeshError("SESSION_CLOSED", "Conversation is closed")
        if view.active_run_id == run_id:
            return
        if view.active_run_id is not None:
            raise OpsMeshError("session_busy", "Conversation has an active run", retryable=True)
        await self.sessions.update(
            session_id,
            view.model_copy(update={"active_run_id": run_id}).model_dump(mode="json"),
            view.revision,
        )

    async def active_claims(self) -> dict[str, str]:
        """Select occupied sessions for runtime recovery without loading conversation history."""
        self.sessions.database.require_table(self.sessions.table.name)
        active = self.sessions.table.c.data["active_run_id"].as_string()
        query = select(self.sessions.table.c.id, active).where(active.is_not(None))
        async with database_connection(self.sessions.database.engine) as connection:
            return {str(row[0]): str(row[1]) for row in await connection.execute(query)}

    async def release(self, session_id: str, run_id: str) -> None:
        for _ in range(8):
            view = await self.get(session_id)
            if view.active_run_id != run_id:
                return
            try:
                await self.sessions.update(
                    session_id,
                    view.model_copy(update={"active_run_id": None}).model_dump(mode="json"),
                    view.revision,
                )
                return
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
        raise OpsMeshError("CONFLICT", "Session ownership is changing concurrently", retryable=True)

    async def close(self, session_id: str) -> ConversationSession:
        view = await self.get(session_id)
        if view.active_run_id:
            raise OpsMeshError("conflict", "Cancel or finish the active run before closing")
        row = await self.sessions.update(
            session_id,
            view.model_copy(update={"status": "closed"}).model_dump(mode="json"),
            view.revision,
        )
        return self._view(row)

    async def append(
        self,
        session_id: str,
        role: Literal["user", "assistant", "system"],
        text: str,
        *,
        message_id: str | None = None,
        run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ConversationMessage:
        await self.get(session_id)
        message_id = message_id or str(uuid4())
        existing = await self.messages.get(message_id)
        if existing is not None:
            if (existing.data["session_id"], existing.data["role"], existing.data["text"]) != (
                session_id,
                role,
                text,
            ):
                raise OpsMeshError("conflict", "Message identifier has different content")
            return ConversationMessage.model_validate(existing.data)
        message = ConversationMessage(
            id=message_id,
            session_id=session_id,
            role=role,
            text=text,
            run_id=run_id,
            created_at=created_at or datetime.now(UTC),
        )
        try:
            await self.messages.create(message.id, message.model_dump(mode="json"))
        except OpsMeshError as error:
            if error.code != "CONFLICT":
                raise
            return await self.append(
                session_id, role, text, message_id=message_id, run_id=run_id, created_at=created_at
            )
        return message

    async def history(self, session_id: str) -> builtins.list[ConversationMessage]:
        await self.get(session_id)
        messages = [
            ConversationMessage.model_validate(row.data)
            for row in await self.messages.list()
            if row.data["session_id"] == session_id
        ]
        return sorted(messages, key=lambda message: (message.created_at, message.id))
