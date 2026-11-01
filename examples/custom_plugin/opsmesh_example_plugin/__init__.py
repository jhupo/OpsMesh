"""A trusted code plugin: import registers declarations and performs no network IO."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from pydantic import BaseModel, Field

from opsmesh.dependencies import (
    get_agent_factories,
    get_code_tools,
    get_run_context,
    get_runtime_extensions,
)
from opsmesh.framework import OpsMeshPluginMetadata, on_command
from opsmesh.framework.adapters import OpsMeshEvent, OpsMeshMessage
from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentSpecification,
    GuardrailBinding,
    HookBinding,
)
from opsmesh.framework.events import DispatchContext
from opsmesh.framework.params import CommandArg, Depends
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.runtime.api import GuardrailDecision

logger = logging.getLogger(__name__)

__plugin_meta__ = OpsMeshPluginMetadata(
    name="Example assistant",
    description="Command handlers, scoped dependencies and deployed runtime declarations",
    usage="/hello [name]",
    type="application",
    extra={"version": "1", "factories": ["example.assistant@1"]},
)


class AssistantFactoryConfiguration(OpsMeshContract):
    agent_id: str = Field(min_length=1)
    name: str = "Example assistant"
    model_profile_ref: str


def build_assistant(configuration: BaseModel, context: dict[str, Any]) -> AgentSpecification:
    """Factories receive validated configuration and can compute full definitions."""
    if not isinstance(configuration, AssistantFactoryConfiguration):
        raise TypeError("Unexpected factory configuration")
    return AgentSpecification(
        id=configuration.agent_id,
        name=configuration.name,
        model_profile_ref=configuration.model_profile_ref,
        instructions="Answer clearly and use only authorized tools.",
        hooks=(HookBinding(ref="example.observe@1"),),
        guardrails=(GuardrailBinding(ref="example.input_limit@1", stage="input"),),
    )


async def uppercase(arguments: dict[str, Any]) -> dict[str, str]:
    """Run-local services are available only while execution context is bound."""
    context = get_run_context()
    return {"text": str(arguments["text"]).upper(), "run_id": context.run_id}


async def observe_run(context: AgentExecutionContext, event: str, payload: dict[str, Any]) -> None:
    # Do not serialize context, service handles or native SDK objects into events.
    if get_run_context() is not context:
        raise RuntimeError("Runtime context binding differs from the hook argument")
    context.extensions.setdefault("example_hook_events", []).append(event)


def limit_input(context: AgentExecutionContext, data: Any) -> GuardrailDecision:
    return GuardrailDecision(allowed=len(str(data)) <= 10_000, info={"policy": "example.length"})


get_agent_factories().register(
    name="example.assistant",
    version="1",
    config_schema=AssistantFactoryConfiguration,
    factory=build_assistant,
)
get_code_tools().register("example:uppercase@1", uppercase)
get_runtime_extensions().register_hook("example.observe@1", observe_run)
get_runtime_extensions().register_guardrail("example.input_limit@1", limit_input)


async def event_label(context: DispatchContext[OpsMeshEvent]) -> AsyncIterator[str]:
    """A yield dependency is acquired once per event and always released."""
    logger.info("example resource acquired: %s", context.event.id)
    try:
        yield context.event.id
    finally:
        logger.info("example resource released: %s", context.event.id)


def greeting_prefix(label: Annotated[str, Depends(event_label)]) -> str:
    return f"Hello [{label}]"


hello = on_command("hello", priority=10, block=True)


@hello.handle()
async def greet(
    context: DispatchContext[OpsMeshEvent],
    argument: Annotated[OpsMeshMessage, CommandArg()],
    prefix: Annotated[str, Depends(greeting_prefix)],
) -> None:
    name = argument.extract_plain_text().strip() or "world"
    reply = f"{prefix}, {name}!"
    context.values["example_reply"] = reply
    if context.bot is not None:
        await hello.finish(reply)
    await hello.finish()
