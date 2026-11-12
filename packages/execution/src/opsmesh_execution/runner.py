"""Run native SDK agents through framework callbacks and tracing."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypeVar

from agents import (
    Agent,
    RunConfig,
    RunErrorHandlers,
    RunHooks,
    Runner,
    RunResult,
    RunState,
)
from agents.items import TResponseInputItem
from agents.memory import Session
from agents.result import RunResultStreaming

from ._invocation import Invocation
from ._tracing import TraceContext

TContext = TypeVar("TContext")


async def run(
    agent: Agent[TContext],
    message: str | list[TResponseInputItem] | RunState[TContext],
    *,
    context: TContext | None = None,
    session: Session | None = None,
    hooks: RunHooks[TContext] | None = None,
    run_config: RunConfig | None = None,
    max_turns: int | None = 10,
    error_handlers: RunErrorHandlers[TContext] | None = None,
    previous_response_id: str | None = None,
    auto_previous_response_id: bool = False,
    conversation_id: str | None = None,
    plugin_id: str | None = None,
    trace_context: TraceContext | None = None,
) -> RunResult:
    """Execute an agent and return its unmodified native result.

    Args:
        agent: Native Agent or SandboxAgent owned by the consumer plugin.
        message: Native input or an approval continuation state.
        context: Application context passed unchanged to the SDK.
        session: Native conversation history owned by the consumer.
        hooks: Optional native hooks, composed before plugin SDK callbacks.
        run_config: Native run options; tracing policy applies to a copy.
        max_turns: Native execution turn limit.
        error_handlers: Native Runner error handlers.
        previous_response_id: Provider-managed response continuation.
        auto_previous_response_id: Native response ID continuation setting.
        conversation_id: Provider-managed conversation identifier.
        plugin_id: Owner for background calls without a current Matcher.
        trace_context: Explicit user/session tracing attributes.
    """
    async with Invocation(
        agent,
        message,
        context=context,
        hooks=hooks,
        run_config=run_config,
        plugin_id=plugin_id,
        trace_context=trace_context,
    ) as invocation:
        invocation.result = await Runner.run(
            agent,
            message,
            context=context,
            session=session,
            hooks=invocation.hooks,
            run_config=invocation.config,
            max_turns=max_turns,
            error_handlers=error_handlers,
            previous_response_id=previous_response_id,
            auto_previous_response_id=auto_previous_response_id,
            conversation_id=conversation_id,
        )
        return invocation.result


@asynccontextmanager
async def stream(
    agent: Agent[TContext],
    message: str | list[TResponseInputItem] | RunState[TContext],
    *,
    context: TContext | None = None,
    session: Session | None = None,
    hooks: RunHooks[TContext] | None = None,
    run_config: RunConfig | None = None,
    max_turns: int | None = 10,
    error_handlers: RunErrorHandlers[TContext] | None = None,
    previous_response_id: str | None = None,
    auto_previous_response_id: bool = False,
    conversation_id: str | None = None,
    plugin_id: str | None = None,
    trace_context: TraceContext | None = None,
) -> AsyncIterator[RunResultStreaming]:
    """Keep native streaming and callbacks alive until the consumer exits.

    Arguments follow run(). Early exit cancels and awaits SDK cleanup;
    plugins inspect the native result's interruptions for approval handling.
    """
    async with Invocation(
        agent,
        message,
        context=context,
        hooks=hooks,
        run_config=run_config,
        plugin_id=plugin_id,
        trace_context=trace_context,
    ) as invocation:
        result = Runner.run_streamed(
            agent,
            message,
            context=context,
            session=session,
            hooks=invocation.hooks,
            run_config=invocation.config,
            max_turns=max_turns,
            error_handlers=error_handlers,
            previous_response_id=previous_response_id,
            auto_previous_response_id=auto_previous_response_id,
            conversation_id=conversation_id,
        )
        invocation.result = result
        yield result
