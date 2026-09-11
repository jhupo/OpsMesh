import asyncio
from contextlib import suppress
from inspect import isawaitable
from typing import Any

from backend.app.agent_runtime.core.contracts import AgentRunRequest, AgentRuntimeCancellation
from backend.app.agent_runtime.core.errors import AgentRuntimeCancelledError


async def raise_if_cancelled(cancellation: AgentRuntimeCancellation | None) -> None:
    if cancellation is not None and await cancellation.is_cancelled():
        raise AgentRuntimeCancelledError


async def cancel_active_tools(request: AgentRunRequest) -> None:
    executor = request.tool_executor
    cancel = getattr(executor, "cancel_active_tools", None)
    if not callable(cancel):
        return
    result: Any = cancel(context=request.context)
    if isawaitable(result):
        await result


async def stop_cancellation_watcher(task: asyncio.Task[object] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
