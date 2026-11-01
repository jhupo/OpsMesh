"""Finish Run cleanup before acknowledging cancellation to the caller."""

import asyncio
from collections.abc import Awaitable


async def finish_run_cleanup(operation: Awaitable[None]) -> None:
    """Drain cleanup under repeated cancellation; never leave it running detached."""
    task = asyncio.ensure_future(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError
