"""Map registered output types, tool completion and structured delegated input."""

import inspect
from typing import Any

from agents.agent import StopAtTools, ToolsToFinalOutputResult

from opsmesh.framework.agents import AgentSpecification, get_current_execution_context
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import (
    AgentToolInput,
    RuntimeExtensions,
    ToolCompletionDecision,
    ToolExecutionResult,
)

from .output import StructuredOutputSchema
from .serialization import serialize_sdk_value


def output_type(spec: AgentSpecification, extensions: RuntimeExtensions) -> Any:
    if spec.output_type_ref:
        return extensions.output_type(spec.output_type_ref)
    return StructuredOutputSchema(spec.output_schema) if spec.output_schema else None


def tool_completion(spec: AgentSpecification, extensions: RuntimeExtensions) -> Any:
    if not spec.tool_completion_ref:
        return (
            StopAtTools(stop_at_tool_names=list(spec.stop_tools))
            if spec.tool_completion == "stop_at_tools"
            else spec.tool_completion
        )

    async def decide(wrapper: Any, results: list[Any]) -> ToolsToFinalOutputResult:
        callback = extensions.tool_completion(spec.tool_completion_ref or "")
        values = tuple(
            ToolExecutionResult(
                name=item.tool.name,
                output=serialize_sdk_value(item.output),
                interrupted=bool(item.interruptions),
            )
            for item in results
        )
        value = callback(wrapper.context, values)
        value = await value if inspect.isawaitable(value) else value
        if not isinstance(value, ToolCompletionDecision):
            raise OpsMeshError(
                "EXTENSION_INVALID", "Tool completion must return ToolCompletionDecision"
            )
        return ToolsToFinalOutputResult(is_final_output=value.is_final, final_output=value.output)

    return decide


def agent_input_callback(ref: str, extensions: RuntimeExtensions) -> Any:
    async def build(options: Any) -> Any:
        request = AgentToolInput(
            arguments=options.get("params", {}),
            schema_summary=options.get("summary"),
            json_schema=options.get("json_schema"),
        )
        value = extensions.agent_input(ref)(get_current_execution_context(), request)
        value = await value if inspect.isawaitable(value) else value
        if not isinstance(value, str) and not (
            isinstance(value, list) and all(isinstance(item, dict) for item in value)
        ):
            raise OpsMeshError(
                "EXTENSION_INVALID", "Agent input must be text or a list of messages"
            )
        return value

    return build
