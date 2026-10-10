import asyncio
from contextlib import suppress

from backend.app.agents.execution.contracts import AgentRunRequest, AgentRuntimeCancellation
from backend.app.agents.execution.errors import AgentRuntimeCancelledError


async def raise_if_cancelled(cancellation: AgentRuntimeCancellation | None) -> None:
    if cancellation is not None and await cancellation.is_cancelled():
        raise AgentRuntimeCancelledError


async def cancel_active_tools(request: AgentRunRequest) -> None:
    executor = request.tool_executor
    if executor is not None:
        await executor.cancel_active_tools(context=request.context)


async def stop_cancellation_watcher(task: asyncio.Task[object] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
