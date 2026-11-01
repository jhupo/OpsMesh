"""Persistent workspace leases and fenced filesystem operations across service instances."""

import asyncio
import contextlib
import hashlib
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy import select, update

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.transactions import serialized_write

T = TypeVar("T")
_held: ContextVar[dict[str, tuple[asyncio.Task[Any], "WorkspaceMutationLease"]] | None] = (
    ContextVar("workspace_mutation_leases", default=None)
)


class WorkspaceMutationLease:
    def __init__(self, manager: "WorkspaceLockManager", key: str, owner: str, fence: int) -> None:
        self.manager, self.key, self.owner, self.fence = manager, key, owner, fence
        self.lost = False

    async def run(self, operation: Callable[[], T]) -> T:
        return await self.run_async(lambda: asyncio.to_thread(operation))

    async def run_async(
        self, operation: Callable[[], Awaitable[T]], *, mark_content_use: bool = False
    ) -> T:
        """Hold the fence until local or remote I/O completes, including cancellation."""
        if self.lost:
            raise OpsMeshError("WORKSPACE_LEASE_LOST", "Workspace mutation lease was lost")
        table = self.manager.store.table
        async with serialized_write(self.manager.database.engine) as connection:
            row = (
                (
                    await connection.execute(
                        select(table).where(table.c.id == self.key).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            data = dict(row["data"])
            if (
                data["owner"] != self.owner
                or data["fence"] != self.fence
                or data["expires_at"] <= time.time()
            ):
                self.lost = True
                raise OpsMeshError("WORKSPACE_LEASE_LOST", "Workspace mutation was fenced out")
            data["expires_at"] = time.time() + self.manager.lease_seconds
            if mark_content_use:
                data["last_content_use"] = time.time()
            await connection.execute(
                update(table)
                .where(table.c.id == self.key)
                .values(data=data, revision=row["revision"] + 1, updated_at=datetime.now(UTC))
            )
            task = asyncio.ensure_future(operation())
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Releasing the row while external I/O is still running defeats fencing.
                while not task.done():
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.shield(task)
                task.result()
                raise

    async def content_used_within(self, seconds: float) -> bool:
        if not math.isfinite(seconds) or seconds < 0:
            raise OpsMeshError(
                "CONFIG_INVALID", "Content usage window must be finite and nonnegative"
            )
        record = await self.manager.store.get(self.key)
        if record is None:
            return False
        data = record.data
        if data.get("owner") != self.owner or data.get("fence") != self.fence:
            self.lost = True
            raise OpsMeshError("WORKSPACE_LEASE_LOST", "Workspace content lease was fenced out")
        value = data.get("last_content_use")
        return isinstance(value, (int, float)) and time.time() - value < seconds

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.manager.lease_seconds / 3)
            try:
                await self.run(lambda: None)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.lost = True
                return


class WorkspaceLockManager:
    def __init__(
        self,
        database: AsyncDatabase,
        scope: str,
        *,
        lease_seconds: float = 30,
        wait_seconds: float = 30,
    ) -> None:
        if lease_seconds <= 0 or wait_seconds <= 0:
            raise OpsMeshError("CONFIG_INVALID", "Workspace lease durations must be positive")
        self.database, self.scope = database, scope
        self.lease_seconds, self.wait_seconds = lease_seconds, wait_seconds
        self.store = VersionedRecordStore(database, "workspace_mutation_leases")

    async def _acquire(self, key: str) -> WorkspaceMutationLease:
        deadline = time.monotonic() + self.wait_seconds
        owner = uuid4().hex
        while True:
            record = await self.store.get(key)
            now = time.time()
            if record is None or record.data["expires_at"] <= now:
                fence = 1 if record is None else int(record.data["fence"]) + 1
                data: dict[str, Any] = {
                    "owner": owner,
                    "fence": fence,
                    "expires_at": now + self.lease_seconds,
                }
                if record is not None and "last_content_use" in record.data:
                    data["last_content_use"] = record.data["last_content_use"]
                try:
                    if record is None:
                        await self.store.create(key, data)
                    else:
                        await self.store.update(key, data, record.revision)
                    return WorkspaceMutationLease(self, key, owner, fence)
                except OpsMeshError as error:
                    if error.code != "CONFLICT":
                        raise
            if time.monotonic() >= deadline:
                raise OpsMeshError("WORKSPACE_BUSY", "Workspace is being mutated", retryable=True)
            await asyncio.sleep(min(0.05, self.lease_seconds / 5))

    async def _release(self, lease: WorkspaceMutationLease) -> None:
        for _ in range(3):
            record = await self.store.get(lease.key)
            if record is None or record.data["owner"] != lease.owner:
                return
            try:
                await self.store.update(
                    record.id, {**record.data, "owner": None, "expires_at": 0}, record.revision
                )
                return
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise

    @asynccontextmanager
    async def hold(self, workspace_id: str) -> AsyncIterator[WorkspaceMutationLease]:
        key = hashlib.sha256(f"{self.scope}:{workspace_id}".encode()).hexdigest()
        task = asyncio.current_task()
        assert task is not None
        held = _held.get() or {}
        current = held.get(key)
        if current is not None and current[0] is task:
            if current[1].lost:
                raise OpsMeshError("WORKSPACE_LEASE_LOST", "Workspace lease was lost")
            yield current[1]
            return
        lease = await self._acquire(key)
        token = _held.set({**held, key: (task, lease)})
        heartbeat = asyncio.create_task(lease.heartbeat())
        try:
            yield lease
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            _held.reset(token)
            await self._release(lease)
