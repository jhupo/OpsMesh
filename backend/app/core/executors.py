from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TypeVar

from backend.app.core.config import Settings, get_settings

T = TypeVar("T")

_blocking_executor: ThreadPoolExecutor | None = None
_blocking_executor_workers: int | None = None


def get_blocking_executor(settings: Settings | None = None) -> ThreadPoolExecutor:
    global _blocking_executor, _blocking_executor_workers

    resolved_settings = settings or get_settings()
    workers = resolved_settings.blocking_thread_pool_workers
    if _blocking_executor is None or _blocking_executor_workers != workers:
        shutdown_blocking_executor(wait=False)
        _blocking_executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="chaincloud-blocking",
        )
        _blocking_executor_workers = workers
    return _blocking_executor


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
