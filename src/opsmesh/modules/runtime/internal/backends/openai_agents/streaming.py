"""Translate SDK stream events and drain cancellation before releasing resources."""

import asyncio
from contextlib import suppress
from time import perf_counter
from typing import Any

from agents import Runner

from opsmesh.framework.agents import AgentExecutionContext, AgentExecutionEvent, EventSink


def translate_event(event: Any) -> AgentExecutionEvent | None:
    if event.type == "raw_response_event" and event.data.type == "response.output_text.delta":
        return AgentExecutionEvent(kind="text_delta", payload={"text": event.data.delta})
    if event.type == "agent_updated_stream_event":
        return AgentExecutionEvent(kind="agent_changed", payload={"name": event.new_agent.name})
    if event.type == "run_item_stream_event":
        return AgentExecutionEvent(kind=event.name, payload={"agent_name": event.item.agent.name})
    return None


async def execute_stream(
    agent: Any, input: Any, context: AgentExecutionContext, options: dict[str, Any], emit: EventSink
) -> Any:
    stream = Runner.run_streamed(agent, input, **options)
    started, observed_token = perf_counter(), False
    try:
        async for native in stream.stream_events():
            if context.cancel_event.is_set():
                raise asyncio.CancelledError
            event = translate_event(native)
            if event is not None:
                if event.kind == "text_delta" and not observed_token:
                    observed_token = True
                    if metric := context.services.get("observe_metric"):
                        metric("first_token_seconds", perf_counter() - started)
                await emit(event)
        return stream
    except BaseException:
        stream.cancel()
        # The SDK requires draining after cancellation for task cleanup.
        with suppress(Exception, asyncio.CancelledError):
            async with asyncio.timeout(5):
                async for _ in stream.stream_events():
                    pass
        raise
