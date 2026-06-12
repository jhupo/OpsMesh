from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import TypeVar

from backend.app.core.config import Settings, get_settings

T = TypeVar("T")

_blocking_executor: ThreadPoolExecutor | None = None
_blocking_executor_workers: int | None = None


@dataclass(frozen=True)
class BlockingExecutorSnapshot:
    configured_workers: int
    active_threads: int
    queued_work_items: int
    initialized: bool

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "configured_workers": self.configured_workers,
            "active_threads": self.active_threads,
            "queued_work_items": self.queued_work_items,
            "initialized": self.initialized,
        }


def get_blocking_executor(settings: Settings | None = None) -> ThreadPoolExecutor:
    global _blocking_executor, _blocking_executor_workers

    resolved_settings = settings or get_settings()
    workers = resolved_settings.blocking_thread_pool_workers
    if _blocking_executor is None or _blocking_executor_workers != workers:
        shutdown_blocking_executor(wait=False)
        _blocking_executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="opsmesh-blocking",
        )
        _blocking_executor_workers = workers
    return _blocking_executor


def blocking_executor_snapshot(settings: Settings | None = None) -> BlockingExecutorSnapshot:
    resolved_settings = settings or get_settings()
    if _blocking_executor is None:
        return BlockingExecutorSnapshot(
            configured_workers=resolved_settings.blocking_thread_pool_workers,
            active_threads=0,
            queued_work_items=0,
            initialized=False,
        )

    return BlockingExecutorSnapshot(
        configured_workers=_blocking_executor_workers
        or resolved_settings.blocking_thread_pool_workers,
        active_threads=len(_blocking_executor._threads),  # noqa: SLF001
        queued_work_items=_blocking_executor._work_queue.qsize(),  # noqa: SLF001
        initialized=True,
    )


async def run_blocking(
    func: Callable[..., T],
    *args: object,
    settings: Settings | None = None,
    **kwargs: object,
) -> T:
    loop = asyncio.get_running_loop()
    executor = get_blocking_executor(settings)
    call = partial(func, *args, **kwargs)
    return await loop.run_in_executor(executor, call)


def shutdown_blocking_executor(*, wait: bool = True) -> None:
    global _blocking_executor, _blocking_executor_workers

    if _blocking_executor is not None:
        _blocking_executor.shutdown(wait=wait, cancel_futures=not wait)
    _blocking_executor = None
    _blocking_executor_workers = None
