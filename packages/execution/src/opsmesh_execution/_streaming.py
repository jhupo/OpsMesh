"""Bounded native SDK stream cleanup; no execution orchestration."""

import asyncio
import logging
from typing import Any

from agents.result import RunResultStreaming

logger = logging.getLogger(__name__)
_pending_cleanup: set[asyncio.Task[Any]] = set()


def was_cancelled(result: RunResultStreaming) -> bool:
    return getattr(result, "_cancel_mode", "none") != "none"


async def close_stream(
    result: RunResultStreaming, timeout: float, *, cancel: bool = False
) -> bool:
    """Cancel if needed and wait within a bounded SDK cleanup budget."""
    if cancel:
        result.cancel("immediate")
    cleanup = asyncio.create_task(_close_stream_tasks(result, timeout))
    cancellation: asyncio.CancelledError | None = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as error:
            if cleanup.cancelled():
                raise
            cancellation = cancellation or error
    cleaned = cleanup.result()
    if cancellation is not None:
        raise cancellation
    return cleaned


async def _close_stream_tasks(
    result: RunResultStreaming, timeout: float
) -> bool:
    """Keep SDK cleanup alive when its consumer receives Task.cancel()."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    cleaned = await _wait_task(
        result.run_loop_task, _remaining(deadline), error_is_failure=False
    )
    await asyncio.sleep(0)
    for name in ("_model_provider_cleanup_task", "_sandbox_cleanup_task"):
        task = getattr(result, name, None)
        if task is None:
            continue
        cleaned = (await _wait_task(task, _remaining(deadline))) and cleaned
    return cleaned


async def _wait_task(
    task: asyncio.Task[Any] | None,
    timeout: float,
    *,
    error_is_failure: bool = True,
) -> bool:
    if task is None:
        return True
    if task.done():
        if not error_is_failure or task.cancelled():
            return True
        try:
            return task.exception() is None
        except BaseException:
            return False
    if timeout <= 0:
        logger.warning("SDK stream cleanup exceeded its time budget")
        task.cancel()
        await _settle(task)
        return False
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return True
    except TimeoutError:
        logger.warning("SDK stream cleanup exceeded its time budget")
        task.cancel()
        await _settle(task)
        return False
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        return True
    except BaseException:
        logger.debug("SDK stream cleanup task ended with an exception")
        return not error_is_failure


async def _settle(task: asyncio.Task[Any]) -> None:
    _, pending = await asyncio.wait({task}, timeout=1.0)
    if pending:
        _pending_cleanup.add(task)
        task.add_done_callback(_release_cleanup)
        logger.warning("SDK cleanup task is still pending after cancellation")
    else:
        _release_cleanup(task)


def _release_cleanup(task: asyncio.Task[Any]) -> None:
    _pending_cleanup.discard(task)
    if not task.cancelled():
        task.exception()


def _remaining(deadline: float) -> float:
    return deadline - asyncio.get_running_loop().time()
