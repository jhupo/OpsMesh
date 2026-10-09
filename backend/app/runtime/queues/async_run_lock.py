"""Run ownership renewal on the worker loop, with reserved blocking I/O capacity."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from backend.app.runtime.queues.execution_control import (
    ExecutionControl,
    ExecutionOwnershipLostError,
    current_execution_control,
    execution_control,
)
from backend.app.runtime.queues.service import RedisQueue
from backend.app.shared.concurrency import BlockingIO


@asynccontextmanager
async def async_run_lock(
    queue: RedisQueue, workspace_id: str, run_id: str, io: BlockingIO, *, ttl_seconds: int = 600
) -> AsyncIterator[bool]:
    if ttl_seconds < 1:
        raise ValueError("Run lock TTL must be positive")
    lock = queue.redis.lock(
        queue.keys.run_lock(workspace_id, run_id),
        timeout=ttl_seconds,
        blocking=False,
        thread_local=False,
    )
    try:
        acquired = await io.run(lock.acquire)
    except BaseException:
        # Acquisition may have completed in its adapter thread when the caller
        # was cancelled. Release only our own token before propagating it.
        if await io.run(lock.owned):
            await io.run(lock.release)
        raise
    if not acquired:
        yield False
        return
    control = current_execution_control() or ExecutionControl()

    async def renew() -> None:
        while True:
            await asyncio.sleep(ttl_seconds / 3)
            try:
                await io.run(lambda: lock.extend(ttl_seconds, replace_ttl=True))
            except Exception:
                control.ownership_lost.set()
                return

    renewal = asyncio.create_task(renew(), name=f"run-lock:{run_id}")
    try:
        with execution_control(control):
            yield True
            control.check_ownership()
            if not await io.run(lock.owned):
                control.ownership_lost.set()
                raise ExecutionOwnershipLostError("Run ownership was lost before completion")
    finally:
        renewal.cancel()
        with suppress(asyncio.CancelledError):
            await renewal
        try:
            await io.run(lock.release)
        except Exception as exc:
            control.ownership_lost.set()
            raise ExecutionOwnershipLostError("Run ownership could not be released") from exc
