from contextlib import AsyncExitStack
from time import monotonic
from typing import Any

from agents import Agent, RunConfig, RunContextWrapper, Runner, RunState, Tool, function_tool
from agents.result import RunResultBase
from openai.types.responses import ResponseTextDeltaEvent
from opsmesh_sdk import InvocationContext, PluginContext, Suspended

from .schemas import AgentConfiguration


async def inspect_identity(context: RunContextWrapper[InvocationContext]) -> dict[str, Any]:
    """Return the authenticated caller's identity and this invocation's identifier."""
    user = context.context.user
    return {
        "user_id": user.user_id,
        "roles": sorted(user.roles),
        "group_ids": sorted(user.group_ids),
        "task_id": context.context.task_id,
    }


async def confirm_text(context: RunContextWrapper[InvocationContext], text: str) -> str:
    """Return text only after the caller approves this tool invocation."""
    return text


def output(result: RunResultBase) -> Any:
    if result.interruptions:
        calls = []
        for interruption in result.interruptions:
            raw = interruption.raw_item
            calls.append(raw if isinstance(raw, dict) else raw.model_dump(mode="json"))
        state = result.to_state().to_json(context_serializer=lambda _: {}, strict_context=True)
        return Suspended(
            checkpoint={"run_state": state},
            prompt="Approve all tool calls listed in this request?",
            details={"calls": calls},
        )
    return result.final_output


async def invoke(context: InvocationContext, value: Any) -> Any:
    config = AgentConfiguration.model_validate(context.config)
    model = await context.resources.model(
        config.connection_id, config.model, reserve_tokens=config.reserve_tokens
    )
    tools: list[Tool] = [
        function_tool(inspect_identity),
        function_tool(confirm_text, needs_approval=True),
    ]
    tools.extend([await context.dependencies.tool(alias) for alias in config.tools])
    async with AsyncExitStack() as stack:
        servers = [
            await stack.enter_async_context(context.dependencies.mcp(alias))
            for alias in config.mcps
        ]
        agent = Agent[InvocationContext](
            name="Assistant",
            instructions=config.instructions,
            model=model,
            tools=tools,
            mcp_servers=servers,
        )
        run_input: Any = value
        if context.checkpoint is not None:
            if context.decision is None or type(context.decision.value) is not bool:
                raise ValueError("A boolean approval response is required")
            state = await RunState.from_json(
                agent,
                context.checkpoint["run_state"],
                context_override=RunContextWrapper(context),
                strict_context=True,
            )
            for interruption in state.get_interruptions():
                if context.decision.value:
                    state.approve(interruption)
                else:
                    state.reject(interruption)
            run_input = state
        result = Runner.run_streamed(
            agent,
            run_input,
            context=context,
            max_turns=32,
            run_config=RunConfig(trace_include_sensitive_data=False),
            session=context.session.history,
        )
        pending, last_sent = "", monotonic()
        try:
            async for event in result.stream_events():
                if event.type == "raw_response_event" and isinstance(
                    event.data, ResponseTextDeltaEvent
                ):
                    pending += event.data.delta
                    if len(pending) >= 512 or monotonic() - last_sent >= 0.1:
                        await context.events.emit("text.delta", {"delta": pending})
                        pending, last_sent = "", monotonic()
            if pending:
                await context.events.emit("text.delta", {"delta": pending})
        finally:
            if not result.is_complete:
                result.cancel()
                async for _ in result.stream_events():
                    pass
        if result.run_loop_exception is not None:
            raise result.run_loop_exception
        return output(result)


def setup(context: PluginContext) -> None:
    context.entry("chat", invoke, resume=invoke)
    context.tool("identity", function_tool(inspect_identity))
