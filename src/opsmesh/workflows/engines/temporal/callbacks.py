"""Native callback proxies: Workflow decisions stay native; callback I/O uses Activities."""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrail,
    OutputGuardrail,
    RunContextWrapper,
)
from agents.items import ModelResponse, TResponseInputItem
from agents.lifecycle import AgentHooksBase
from agents.prompts import GenerateDynamicPromptData
from agents.run_context import AgentHookContext
from agents.tool import Tool
from agents.tool_context import ToolContext
from agents.usage import Usage
from pydantic import BaseModel, ConfigDict, TypeAdapter

from opsmesh.agents.callbacks.registry import CallbackKind, CallbackRegistry
from opsmesh.agents.config.callbacks import CallbackRef
from opsmesh.execution.preparation import AgentSnapshot
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.serialization import canonical_json

SUPPORTED_CALLBACKS = {
    "instructions",
    "prompt",
    "input_guardrail",
    "output_guardrail",
    "agent_hooks",
}
JSON_ADAPTER: TypeAdapter[Any] = TypeAdapter(Any)


class CallbackDescription(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: CallbackKind
    reference: CallbackRef
    revision: str
    name: str | None = None
    run_in_parallel: bool = True


def callback_references(snapshot: AgentSnapshot) -> list[tuple[CallbackKind, CallbackRef]]:
    references: list[tuple[CallbackKind, CallbackRef]] = []

    def add(kind: CallbackKind, value: object) -> None:
        if isinstance(value, CallbackRef):
            references.append((kind, value))

    for config in snapshot.definitions.values():
        add("instructions", config.instructions)
        add("prompt", config.prompt)
        add("agent_hooks", config.hooks)
        add("tool_use_behavior", config.tool_use_behavior)
        add("tool_error", config.mcp_config.failure_error_function)
        for reference in config.input_guardrails:
            add("input_guardrail", reference)
        for reference in config.output_guardrails:
            add("output_guardrail", reference)
        if config.output_type is not None and config.output_type.type == "registered":
            add("output_type", config.output_type.reference)
        for server in config.mcp_servers:
            add("tool_approval", server.require_approval)
        for tool in config.tools:
            if tool.type == "agent":
                for field, kind in {
                    "custom_output_extractor": "output_extractor",
                    "is_enabled": "tool_enabled",
                    "on_stream": "stream",
                    "hooks": "run_hooks",
                    "failure_error_function": "tool_error",
                    "needs_approval": "tool_approval",
                    "parameters": "input_type",
                    "input_builder": "input_builder",
                }.items():
                    add(cast(CallbackKind, kind), getattr(tool, field))
        for handoff in config.handoffs:
            add("on_handoff", handoff.on_handoff)
            add("input_type", handoff.input_type)
            add("handoff_filter", handoff.input_filter)
            add("tool_enabled", handoff.is_enabled)
        if config.type == "sandbox":
            add("instructions", config.base_instructions)
            for capability in config.capabilities or []:
                add("capability", capability.factory)
                add("configure_tools", capability.configure_tools)
                add("skill_source", capability.lazy_from)
    return references


def describe_callbacks(
    registry: CallbackRegistry, snapshot: AgentSnapshot
) -> list[CallbackDescription]:
    descriptions: dict[str, CallbackDescription] = {}
    references = callback_references(snapshot)
    names = [config.name for config in snapshot.definitions.values()]
    if references and len(names) != len(set(names)):
        raise InvalidArgumentError("Temporal callback targets require unique Agent names")
    for kind, reference in references:
        if kind not in SUPPORTED_CALLBACKS:
            raise InvalidArgumentError(f"Temporal does not support the {kind} callback contract")
        try:
            value = registry.resolve(kind, reference)
        except KeyError as error:
            raise InvalidArgumentError(str(error)) from error
        revision = registry.revision(kind, reference)
        if not revision:
            raise InvalidArgumentError(
                "Temporal callback registrations require an immutable revision"
            )
        description = CallbackDescription(kind=kind, reference=reference, revision=revision)
        if isinstance(value, InputGuardrail | OutputGuardrail):
            description = description.model_copy(update={"name": value.get_name()})
        if isinstance(value, InputGuardrail):
            description = description.model_copy(update={"run_in_parallel": value.run_in_parallel})
        descriptions[callback_key(kind, reference)] = description
    return list(descriptions.values())


def callback_key(kind: CallbackKind, reference: CallbackRef) -> str:
    return kind + ":" + canonical_json(reference.model_dump(mode="json"))


CallbackInvoker = Callable[
    [CallbackDescription, str, RunContextWrapper[Any], Agent[Any], dict[str, Any]], Awaitable[Any]
]


def register_callbacks(
    registry: CallbackRegistry, descriptions: list[CallbackDescription], invoke: CallbackInvoker
) -> None:
    indexed = {callback_key(item.kind, item.reference): item for item in descriptions}

    def factory(kind: CallbackKind, identifier: str) -> Callable[..., Any]:
        def create(config: dict[str, Any]) -> Any:
            reference = CallbackRef(callback_id=identifier, config=config)
            description = indexed[callback_key(kind, reference)]

            async def call(context: Any, agent: Agent[Any], **data: Any) -> Any:
                return await invoke(description, "call", context, agent, data)

            if kind == "instructions":
                return call
            if kind == "prompt":

                async def prompt(data: GenerateDynamicPromptData) -> Any:
                    return await call(data.context, data.agent)

                return prompt
            if kind == "input_guardrail":

                async def input_guardrail(context: Any, agent: Any, input: Any) -> Any:
                    result = await call(context, agent, input=input)
                    return TypeAdapter(GuardrailFunctionOutput).validate_python(result)

                return InputGuardrail(
                    guardrail_function=input_guardrail,
                    name=description.name,
                    run_in_parallel=description.run_in_parallel,
                )
            if kind == "output_guardrail":

                async def output_guardrail(context: Any, agent: Any, output: Any) -> Any:
                    result = await call(context, agent, output=output)
                    return TypeAdapter(GuardrailFunctionOutput).validate_python(result)

                return OutputGuardrail(guardrail_function=output_guardrail, name=description.name)
            if kind == "agent_hooks":
                return ActivityAgentHooks(description, invoke)
            raise InvalidArgumentError(f"Unsupported Temporal callback: {kind}")

        return create

    for kind, identifier in dict.fromkeys(
        (item.kind, item.reference.callback_id) for item in descriptions
    ):
        registry.register(kind, identifier, factory(kind, identifier))


def wrapper_payload(context: RunContextWrapper[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "usage": TypeAdapter(Usage).dump_python(context.usage, mode="json"),
        "turn_input": JSON_ADAPTER.dump_python(context.turn_input, mode="json"),
        "tool_input": JSON_ADAPTER.dump_python(context.tool_input, mode="json"),
    }
    if isinstance(context, AgentHookContext):
        result["kind"] = "agent"
    elif isinstance(context, ToolContext):
        result.update(
            kind="tool",
            tool_name=context.tool_name,
            tool_call_id=context.tool_call_id,
            tool_arguments=context.tool_arguments,
        )
    else:
        result["kind"] = "run"
    return result


class ActivityAgentHooks(AgentHooksBase[Any, Agent[Any]]):
    def __init__(self, description: CallbackDescription, invoke: CallbackInvoker) -> None:
        self.description, self.invoke = description, invoke

    async def on_start(self, context: AgentHookContext[Any], agent: Agent[Any]) -> None:
        await self.invoke(self.description, "on_start", context, agent, {})

    async def on_end(self, context: AgentHookContext[Any], agent: Agent[Any], output: Any) -> None:
        await self.invoke(self.description, "on_end", context, agent, {"output": output})

    async def on_handoff(
        self, context: RunContextWrapper[Any], agent: Agent[Any], source: Agent[Any]
    ) -> None:
        await self.invoke(self.description, "on_handoff", context, agent, {"source": source.name})

    async def on_tool_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any], tool: Tool
    ) -> None:
        await self.invoke(self.description, "on_tool_start", context, agent, {"tool": tool.name})

    async def on_tool_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], tool: Tool, result: object
    ) -> None:
        await self.invoke(
            self.description, "on_tool_end", context, agent, {"tool": tool.name, "result": result}
        )

    async def on_llm_start(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        system_prompt: str | None,
        input_items: list[TResponseInputItem],
    ) -> None:
        await self.invoke(
            self.description,
            "on_llm_start",
            context,
            agent,
            {"system_prompt": system_prompt, "input_items": input_items},
        )

    async def on_llm_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], response: ModelResponse
    ) -> None:
        await self.invoke(
            self.description,
            "on_llm_end",
            context,
            agent,
            {"response": TypeAdapter(ModelResponse).dump_python(response, mode="json")},
        )
