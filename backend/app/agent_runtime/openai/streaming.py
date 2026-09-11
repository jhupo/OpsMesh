from __future__ import annotations

import asyncio

from agents import Agent, RunConfig, RunHooks, Runner, RunResultStreaming, RunState, Session
from agents.items import TResponseInputItem

from backend.app.agent_runtime.cancellation import (
    cancel_active_tools,
    raise_if_cancelled,
    stop_cancellation_watcher,
)
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
)
from backend.app.agent_runtime.core.errors import AgentRuntimeCancelledError
from backend.app.agent_runtime.execution_observer import AgentRuntimeExecutionObserver
from backend.app.agent_runtime.openai.results import runtime_stream_event_from_sdk_item


async def run_openai_streamed(
    *,
    request: AgentRunRequest,
    agent: Agent[AgentRuntimeContext],
    runner_input: str | list[TResponseInputItem] | RunState[AgentRuntimeContext],
    hooks: RunHooks[AgentRuntimeContext],
    run_config: RunConfig | None,
    session: Session | None,
    observer: AgentRuntimeExecutionObserver,
) -> RunResultStreaming:
    await raise_if_cancelled(request.cancellation)
    result = Runner.run_streamed(
        agent,
        runner_input,
        context=request.context,
        max_turns=request.max_turns,
        hooks=hooks,
        run_config=run_config,
        previous_response_id=request.previous_response_id,
        conversation_id=request.conversation_id,
        session=session,
    )
    cancelled = asyncio.Event()

    async def watch_cancellation() -> None:
        cancellation = request.cancellation
        if cancellation is None:
            return
        await cancellation.wait_cancelled()
        cancelled.set()
        result.cancel()
        await cancel_active_tools(request)

    watcher: asyncio.Task[object] | None = None
    if request.cancellation is not None:
        watcher = asyncio.create_task(watch_cancellation())
    try:
        async for item in result.stream_events():
            if request.stream:
                mapped = runtime_stream_event_from_sdk_item(
                    item,
                    sequence=len(observer.stream_events) + 1,
                )
                if mapped is not None:
                    observer.stream(
                        mapped.event_type,
                        payload=mapped.payload,
                        delta=mapped.delta,
                    )
    finally:
        await stop_cancellation_watcher(watcher)
    if cancelled.is_set():
        raise AgentRuntimeCancelledError
    return result
