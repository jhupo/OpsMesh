"""Bounded adapters for blocking infrastructure, never an Agent SDK execution pool."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from contextvars import copy_context
from types import TracebackType
from typing import TypeVar

T = TypeVar("T")


class BlockingIO:
    def __init__(self, concurrency: int, *, name: str) -> None:
        if concurrency < 1:
            raise ValueError("Blocking I/O concurrency must be positive")
        self._slots = asyncio.Semaphore(concurrency)
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix=name)

    async def run(self, operation: Callable[[], T]) -> T:
        async with self._slots:
            context = copy_context()
            pending = asyncio.get_running_loop().run_in_executor(
                self._executor, context.run, operation
            )
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                # A cancelled await must not release capacity while the operation still
                # owns its connection/session. Drain it before propagating cancellation.
                while not pending.done():
                    with suppress(asyncio.CancelledError):
                        await asyncio.shield(pending)
                with suppress(Exception):
                    pending.result()
                raise

    def __enter__(self) -> BlockingIO:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
