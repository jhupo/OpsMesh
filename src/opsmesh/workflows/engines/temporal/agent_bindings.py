"""Freeze resource descriptions in Activities and assemble pure Workflow proxies."""

import inspect
import json
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from agents import Agent, FunctionTool, ModelSettings, RunContextWrapper
from agents.mcp import MCPServer
from agents.sandbox import Manifest
from agents.sandbox.entries import BaseEntry
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.openai_agents.workflow import temporal_mcp_server
from temporalio.workflow import ActivityConfig

from opsmesh.agents.assembly import AssemblyBindings
from opsmesh.agents.config.manifest import EntryRef
from opsmesh.agents.config.mcp import MCPServerRef
from opsmesh.agents.config.model import ModelRef, model_settings
from opsmesh.agents.providers.factory import create_model
from opsmesh.agents.skills.catalog import SkillInfo
from opsmesh.agents.tools.registry import ToolInfo
from opsmesh.execution.context import ExecutionContext
from opsmesh.execution.contracts import PreparedAgent
from opsmesh.shared.serialization import canonical_json
from opsmesh.workflows.engines.temporal.callbacks import (
    JSON_ADAPTER,
    CallbackDescription,
    describe_callbacks,
    register_callbacks,
    wrapper_payload,
)

MODEL_PREFIX = "opsmesh-instance:"
ACTIVITY_OPTIONS: ActivityConfig = {
    "start_to_close_timeout": timedelta(minutes=6),
    "retry_policy": RetryPolicy(maximum_attempts=1),
    "cancellation_type": workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
}


class ResourceDescriptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled_tools: dict[str, bool] = Field(default_factory=dict)
    tools: list[ToolInfo] = Field(default_factory=list)
    skills: list[SkillInfo] = Field(default_factory=list)
    entries: dict[str, dict[str, Any]] = Field(default_factory=dict)
    manifests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    models: dict[str, dict[str, Any]] = Field(default_factory=dict)
    callbacks: list[CallbackDescription] = Field(default_factory=list)


def model_name(context: ExecutionContext, agent_id: str, reference: ModelRef) -> str:
    return MODEL_PREFIX + canonical_json(
        {
            "run_id": str(context.run_id),
            "attempt_id": context.attempt_id,
            "agent_id": agent_id,
            "provider_id": reference.provider_id,
            "model_id": reference.model_id,
        }
    )


async def describe_resources(
    source: AssemblyBindings, prepared: PreparedAgent, context: ExecutionContext | None = None
) -> ResourceDescriptions:
    descriptions = ResourceDescriptions(
        tools=source.tools.list(),
        skills=source.skills.list(),
        callbacks=describe_callbacks(source.callbacks, prepared.snapshot),
    )

    for config in prepared.snapshot.definitions.values():
        for tool_reference in config.tools:
            if tool_reference.type != "tool":
                continue
            tool = source.tools.create(tool_reference.tool_id, tool_reference.config)
            if isinstance(tool, FunctionTool):
                enabled = tool.is_enabled
                decision = (
                    enabled(RunContextWrapper(context), Agent(name=config.name))
                    if callable(enabled)
                    else enabled
                )
                descriptions.enabled_tools[tool_reference.tool_id] = (
                    await decision if inspect.isawaitable(decision) else decision
                )

    def capture(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "reference":
                reference = EntryRef.model_validate(value)
                descriptions.entries[reference.entry_id] = source.entry_resolver(
                    reference
                ).model_dump(mode="json")
            elif value.get("template_id") is not None:
                identifier = value["template_id"]
                if source.manifest_resolver is None:
                    raise ValueError("A manifest template requires a resolver")
                descriptions.manifests[identifier] = source.manifest_resolver(
                    identifier
                ).model_dump(mode="json")
            for item in value.values():
                capture(item)
        elif isinstance(value, list):
            for item in value:
                capture(item)

    capture(prepared.snapshot.model_dump(mode="json"))
    for skill in descriptions.skills:
        capture(skill.model_dump(mode="json"))
    async with AsyncExitStack() as lifespan:
        for agent_id, config in prepared.snapshot.definitions.items():
            reference = config.model
            if reference is None:
                raise ValueError("Temporal Agent configurations require a model reference")
            if source.model_resolver is not None:
                _, settings = await source.model_resolver(agent_id, reference, lifespan)
            elif source.credentials is not None:
                _, info = source.providers.select(reference.provider_id, reference.model_id)
                _, settings = await create_model(
                    reference,
                    providers=source.providers,
                    credentials=source.credentials,
                    lifespan=lifespan,
                )
                if settings.max_tokens is None and info.max_output_tokens is not None:
                    settings = settings.resolve(ModelSettings(max_tokens=info.max_output_tokens))
            else:
                raise ValueError("A model resolver is required")
            descriptions.models[agent_id] = settings.to_json_dict() if settings else {}
    return descriptions


def workflow_bindings(
    context: ExecutionContext, resources: ResourceDescriptions
) -> AssemblyBindings:
    bindings = AssemblyBindings()

    async def invoke_callback(
        description: CallbackDescription,
        method: str,
        wrapper: RunContextWrapper[Any],
        agent: Agent[Any],
        data: dict[str, Any],
    ) -> Any:
        return await workflow.execute_activity(
            "execution.instance_callback",
            {
                "run_id": str(context.run_id),
                "attempt_id": context.attempt_id,
                "description": description.model_dump(mode="json"),
                "method": method,
                "agent": agent.name,
                "wrapper": wrapper_payload(wrapper),
                "data": JSON_ADAPTER.dump_python(data, mode="json"),
            },
            result_type=Any,
            **ACTIVITY_OPTIONS,
        )

    register_callbacks(bindings.callbacks, resources.callbacks, invoke_callback)

    async def resolve_model(
        agent_id: str, reference: ModelRef, lifespan: AsyncExitStack
    ) -> tuple[str, ModelSettings]:
        return model_name(context, agent_id, reference), model_settings(resources.models[agent_id])

    async def resolve_mcp(
        reference: MCPServerRef, options: dict[str, Any], lifespan: AsyncExitStack
    ) -> MCPServer:
        proxy_options = {key: value for key, value in options.items() if key != "tool_filter"}
        if reference.require_approval is None:
            proxy_options["require_approval"] = "always"
        return temporal_mcp_server(
            "opsmesh.instance",
            activity_config=ACTIVITY_OPTIONS,
            factory_argument={
                "run_id": str(context.run_id),
                "attempt_id": context.attempt_id,
                "reference": reference.model_dump(mode="json"),
            },
            **proxy_options,
        )

    def factory(info: ToolInfo) -> Callable[[dict[str, Any]], FunctionTool]:
        def create(config: dict[str, Any]) -> FunctionTool:
            async def invoke(tool_context: ToolContext[Any], payload: str) -> object:
                return await workflow.execute_activity(
                    "execution.instance_tool",
                    {
                        "run_id": str(context.run_id),
                        "attempt_id": context.attempt_id,
                        "tool_id": info.id,
                        "call_id": tool_context.tool_call_id,
                        "arguments": json.loads(payload),
                        "config": config,
                        "expected_info": info.model_dump(mode="json"),
                    },
                    **ACTIVITY_OPTIONS,
                )

            return FunctionTool(
                is_enabled=resources.enabled_tools.get(info.id, True),
                name=info.name,
                description=info.description,
                params_json_schema=info.parameters,
                on_invoke_tool=invoke,
                needs_approval=info.requires_approval,
                strict_json_schema=False,
            )

        return create

    bindings.model_resolver, bindings.mcp_resolver = resolve_model, resolve_mcp
    bindings.entry_resolver = lambda reference: BaseEntry.parse(
        resources.entries[reference.entry_id]
    )
    bindings.manifest_resolver = lambda identifier: Manifest.model_validate(
        resources.manifests[identifier]
    )
    for info in resources.tools:
        bindings.tools.register(info, factory(info))
    for skill in resources.skills:
        bindings.skills.register(skill)
    return bindings
