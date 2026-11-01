"""Bridge authorized platform tools and agent delegation into SDK objects."""

import inspect
import json
from typing import Any

from agents import Agent, FunctionTool, handoff
from agents.tool import set_function_tool_failure_error_function
from jsonschema import Draft202012Validator

from opsmesh.framework.agents import (
    AgentSpecification,
    AgentToolBinding,
    HandoffBinding,
    ToolBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import RuntimeExtensions

from .delegation import history_filter_callback, output_extractor_callback, visibility_callback
from .extension_mapping import agent_input_callback
from .guardrails import build_guardrail
from .hooks import RegisteredRunHooks, resolve_hooks
from .mcp_config import handle_mcp_failure


def build_function_tool(
    binding: ToolBinding, spec: AgentSpecification, extensions: RuntimeExtensions
) -> FunctionTool:
    Draft202012Validator.check_schema(binding.input_schema)
    validator = Draft202012Validator(binding.input_schema)

    async def invoke(wrapper: Any, arguments: str) -> Any:
        parsed = json.loads(arguments)
        validator.validate(parsed)
        try:
            return await wrapper.context.invoke_tool(
                binding.tool_ref or binding.name, parsed, wrapper.tool_call_id
            )
        except OpsMeshError as error:
            return await handle_mcp_failure(spec, binding, wrapper, error, extensions)

    guards = [g for g in spec.guardrails if g.tool_name in (None, binding.name, *binding.aliases)]
    native = FunctionTool(
        name=binding.name,
        description=binding.description,
        params_json_schema=binding.input_schema,
        on_invoke_tool=invoke,
        strict_json_schema=binding.strict_schema,
        needs_approval=binding.requires_approval,
        defer_loading=binding.defer_loading,
        timeout_seconds=binding.timeout_seconds,
        timeout_behavior="raise_exception",
        is_enabled=visibility_callback(
            binding.enabled_ref,
            {
                "kind": "mcp_tool" if binding.resource_ref else "tool",
                "name": binding.name,
                "server_ref": binding.resource_ref,
                "source_name": binding.source_name,
            },
            extensions,
        ),
        tool_input_guardrails=[
            build_guardrail(extensions, g) for g in guards if g.stage == "tool_input"
        ],
        tool_output_guardrails=[
            build_guardrail(extensions, g) for g in guards if g.stage == "tool_output"
        ],
    )
    # Only the platform's explicitly configured MCP formatter may convert failures
    # into model text. The SDK's default fallback would also swallow revocations.
    return set_function_tool_failure_error_function(native, None)


def build_handoff(binding: HandoffBinding, target: Any, extensions: RuntimeExtensions) -> Any:
    native = handoff(
        target, tool_name_override=binding.tool_name, tool_description_override=binding.description
    )
    schema = binding.input_schema or {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    Draft202012Validator.check_schema(schema)

    async def transfer(wrapper: Any, raw: str) -> Any:
        data = json.loads(raw or "{}")
        Draft202012Validator(schema).validate(data)
        if binding.callback_ref:
            result = extensions.hook(binding.callback_ref)(
                wrapper.context, "handoff", {"target": binding.agent_ref, "input": data}
            )
            if inspect.isawaitable(result):
                await result
        return target

    native.input_json_schema = schema
    native.strict_json_schema = False
    native.on_invoke_handoff = transfer
    native.is_enabled = visibility_callback(
        binding.enabled_ref, {"kind": "handoff", "agent_ref": binding.agent_ref}, extensions
    )
    if binding.input_filter_ref:
        native.input_filter = history_filter_callback(binding.input_filter_ref, extensions)
    return native


def build_agent_tool(
    binding: AgentToolBinding,
    target: Agent[Any],
    child_spec: AgentSpecification,
    config: Any,
    extensions: RuntimeExtensions,
    parent_spec: AgentSpecification,
) -> FunctionTool:
    tool = target.as_tool(
        binding.name,
        binding.description,
        max_turns=binding.max_turns,
        needs_approval=binding.requires_approval,
        run_config=config,
        is_enabled=visibility_callback(
            binding.enabled_ref, {"kind": "agent_tool", "agent_ref": binding.agent_ref}, extensions
        ),
        custom_output_extractor=output_extractor_callback(binding.output_extractor_ref, extensions)
        if binding.output_extractor_ref
        else None,
        failure_error_function=None,
        parameters=extensions.output_type(binding.input_type_ref)
        if binding.input_type_ref
        else None,
        input_builder=agent_input_callback(binding.input_builder_ref, extensions)
        if binding.input_builder_ref
        else None,
        include_input_schema=binding.include_input_schema,
        hooks=RegisteredRunHooks(resolve_hooks(child_spec, extensions, "run")),
    )
    guards = [guard for guard in parent_spec.guardrails if guard.tool_name in (None, binding.name)]
    tool.tool_input_guardrails = [
        build_guardrail(extensions, guard) for guard in guards if guard.stage == "tool_input"
    ]
    tool.tool_output_guardrails = [
        build_guardrail(extensions, guard) for guard in guards if guard.stage == "tool_output"
    ]
    return tool
