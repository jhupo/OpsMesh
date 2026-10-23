"""Native SQLiteSession persistence and the engine independent SDK Session protocol."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast
from uuid import UUID

from agents import SQLiteSession
from agents.items import TResponseInputItem
from agents.memory.session_settings import SessionSettings

from opsmesh.execution.contracts import ExecutionIdentity, HistoryCall, SessionReference
from opsmesh.resources.storage.local import LocalFileStorage
from opsmesh.shared.serialization import json_safe_value


def native_session(
    directory: Path, user_id: UUID, session_id: UUID, generation: int
) -> SQLiteSession:
    database = directory / ".opsmesh" / "session" / "sessions.sqlite3"
    if os.name == "posix":
        LocalFileStorage(directory).ensure_directory(".opsmesh/session")
        try:
            descriptor = os.open(
                database, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o660
            )
        except FileExistsError:
            pass
        else:
            try:
                os.fchmod(descriptor, 0o660)
            finally:
                os.close(descriptor)
    else:
        database.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteSession(f"{user_id}:{session_id}:{generation}", db_path=database)


class NativeHistory:
    def __init__(
        self,
        directory: Path,
        *,
        authorize: Callable[[HistoryCall], Awaitable[None]],
    ) -> None:
        self.directory = directory
        self.authorize = authorize

    async def operate(self, request: HistoryCall) -> object:
        # Must reload user, active Run ownership, Session generation and FIFO
        # admission. A UUID or a previously prepared execution is not permission.
        await self.authorize(request)
        creation = asyncio.create_task(
            asyncio.to_thread(
                native_session,
                self.directory,
                request.identity.user_id,
                request.reference.session_id,
                request.reference.generation,
            )
        )
        try:
            history = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # Native initialization opens a connection and cannot be interrupted
            # halfway through a worker thread. Reclaim it before acknowledging.
            history = await creation
            await asyncio.to_thread(history.close)
            raise
        try:
            if request.method == "get_items":
                return json_safe_value(await history.get_items(request.limit))
            if request.method == "add_items":
                await history.add_items(cast("list[TResponseInputItem]", request.items))
                return None
            if request.method == "pop_item":
                return json_safe_value(await history.pop_item())
            await history.clear_session()
            return None
        finally:
            # Do not acknowledge cancellation while a native mutation/connection
            # close is still using this shared database.
            task = asyncio.create_task(asyncio.to_thread(history.close))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise


class HistorySession:
    """SDK Session protocol backed by authorized native storage operations."""

    session_settings: SessionSettings | None = None

    def __init__(
        self, storage: NativeHistory, identity: ExecutionIdentity, reference: SessionReference
    ) -> None:
        self.storage, self.identity, self.reference = storage, identity, reference
        self.session_id = f"{identity.user_id}:{reference.session_id}:{reference.generation}"

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        return cast(
            "list[TResponseInputItem]",
            await self.storage.operate(
                HistoryCall(
                    identity=self.identity,
                    reference=self.reference,
                    method="get_items",
                    limit=limit,
                )
            ),
        )

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await self.storage.operate(
            HistoryCall(
                identity=self.identity,
                reference=self.reference,
                method="add_items",
                items=cast("list[dict[str, object]]", items),
            )
        )

    async def pop_item(self) -> TResponseInputItem | None:
        return cast(
            "TResponseInputItem | None",
            await self.storage.operate(
                HistoryCall(
                    identity=self.identity,
                    reference=self.reference,
                    method="pop_item",
                )
            ),
        )

    async def clear_session(self) -> None:
        await self.storage.operate(
            HistoryCall(
                identity=self.identity,
                reference=self.reference,
                method="clear_session",
            )
        )
