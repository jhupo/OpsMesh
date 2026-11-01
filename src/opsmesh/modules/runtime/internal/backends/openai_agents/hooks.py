"""Convert native SDK callbacks to neutral registered hooks."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agents import AgentHooks, RunHooks
from pydantic_core import to_jsonable_python

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification
from opsmesh.modules.runtime.api import Hook, RuntimeExtensions

from .execution_state import execution_state
from .usage import begin_request, publish_request_usage


@dataclass(frozen=True)
class BoundHook:
    callback: Hook
    include_payload: bool = False


def resolve_hooks(
    spec: AgentSpecification, extensions: RuntimeExtensions, scope: str
) -> list[BoundHook]:
    return [
        BoundHook(extensions.hook(binding.ref), binding.include_payload)
        for binding in spec.hooks
        if binding.scope == scope
    ]


async def dispatch(
    callbacks: Sequence[Hook | BoundHook],
    context: AgentExecutionContext,
    stage: str,
    agent: Any,
    content: dict[str, Any] | None = None,
    **payload: Any,
) -> None:
    for registration in callbacks:
        callback = registration.callback if isinstance(registration, BoundHook) else registration
        data = {"agent_name": agent.name, **payload}
        if isinstance(registration, BoundHook) and registration.include_payload and content:
            # A detached, SDK-neutral value prevents a plugin observer from
            # accidentally mutating the request or result consumed by Runner.
            data.update(to_jsonable_python(content, fallback=lambda item: type(item).__name__))
        result = callback(context, stage, data)
        if inspect.isawaitable(result):
            await result


class RegisteredRunHooks(RunHooks[AgentExecutionContext]):
    def __init__(self, callbacks: Sequence[Hook | BoundHook]) -> None:
        self.callbacks = callbacks

    async def on_agent_start(self, context: Any, agent: Any) -> None:
        await dispatch(self.callbacks, context.context, "agent_start", agent)

    async def on_agent_end(self, context: Any, agent: Any, output: Any) -> None:
        await dispatch(
            self.callbacks, context.context, "agent_end", agent, content={"output": output}
        )

    async def on_llm_start(
        self, context: Any, agent: Any, system_prompt: Any, input_items: Any
    ) -> None:
        authorize = context.context.services.authorize_model_request
        if authorize is not None:
            profile = execution_state(context.context).agent_model_profiles.get(agent.name)
            result = authorize(profile)
            if inspect.isawaitable(result):
                await result
        begin_request(context.context, agent, request_scope=context)
        await dispatch(
            self.callbacks,
            context.context,
            "model_start",
            agent,
            input_count=len(input_items),
            content={"system_prompt": system_prompt, "input_items": input_items},
        )

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        await publish_request_usage(context.context, agent, response, request_scope=context)
        await dispatch(
            self.callbacks,
            context.context,
            "model_end",
            agent,
            response_id=response.response_id,
            usage=_usage(response),
            content={"output_items": response.output},
        )

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        await dispatch(
            self.callbacks,
            context.context,
            "tool_start",
            agent,
            tool_name=tool.name,
            call_id=getattr(context, "tool_call_id", None),
            content={"arguments": getattr(context, "tool_arguments", None)},
        )

    async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: Any) -> None:
        await dispatch(
            self.callbacks,
            context.context,
            "tool_end",
            agent,
            tool_name=tool.name,
            call_id=getattr(context, "tool_call_id", None),
            content={"result": result},
        )

    async def on_handoff(self, context: Any, from_agent: Any, to_agent: Any) -> None:
        await dispatch(self.callbacks, context.context, "handoff", from_agent, target=to_agent.name)


class RegisteredAgentHooks(AgentHooks[AgentExecutionContext]):
    def __init__(self, callbacks: Sequence[Hook | BoundHook]) -> None:
        self.callbacks = callbacks

    async def on_start(self, context: Any, agent: Any) -> None:
        await dispatch(self.callbacks, context.context, "agent_start", agent)

    async def on_end(self, context: Any, agent: Any, output: Any) -> None:
        await dispatch(
            self.callbacks, context.context, "agent_end", agent, content={"output": output}
        )

    async def on_llm_start(
        self, context: Any, agent: Any, system_prompt: Any, input_items: Any
    ) -> None:
        await dispatch(
            self.callbacks,
            context.context,
            "model_start",
            agent,
            input_count=len(input_items),
            content={"system_prompt": system_prompt, "input_items": input_items},
        )

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        await dispatch(
            self.callbacks,
            context.context,
            "model_end",
            agent,
            response_id=response.response_id,
            usage=_usage(response),
            content={"output_items": response.output},
        )

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        await dispatch(
            self.callbacks,
            context.context,
            "tool_start",
            agent,
            tool_name=tool.name,
            call_id=getattr(context, "tool_call_id", None),
            content={"arguments": getattr(context, "tool_arguments", None)},
        )

    async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: Any) -> None:
        await dispatch(
            self.callbacks,
            context.context,
            "tool_end",
            agent,
            tool_name=tool.name,
            call_id=getattr(context, "tool_call_id", None),
            content={"result": result},
        )

    async def on_handoff(self, context: Any, agent: Any, source: Any) -> None:
        await dispatch(self.callbacks, context.context, "handoff", source, target=agent.name)


def _usage(response: Any) -> dict[str, int]:
    return {
        field: int(getattr(response.usage, field, 0))
        for field in ("input_tokens", "output_tokens", "total_tokens")
    }
