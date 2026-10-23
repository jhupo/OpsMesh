"""Construct SDK tools; no custom invocation loop or tool argument parser."""

from dataclasses import fields
from typing import Any

from agents import Agent, Tool
from agents.tool import (
    CodeInterpreterTool,
    FileSearchTool,
    HostedMCPTool,
    ImageGenerationTool,
    ProgrammaticToolCallingTool,
    ToolSearchTool,
    WebSearchTool,
)
from pydantic import TypeAdapter

from opsmesh.agents.callbacks import CallbackRegistry
from opsmesh.agents.callbacks.registry import CallbackKind
from opsmesh.agents.config.callbacks import CallbackRef
from opsmesh.agents.config.tools import AgentToolRef, NativeToolConfig, ToolRef

from .registry import ToolRegistry

NATIVE_TOOLS: dict[str, Any] = {
    "web_search": WebSearchTool,
    "file_search": FileSearchTool,
    "code_interpreter": CodeInterpreterTool,
    "image_generation": ImageGenerationTool,
    "hosted_mcp": HostedMCPTool,
    "tool_search": ToolSearchTool,
    "programmatic_tool_calling": ProgrammaticToolCallingTool,
}


def resolve_tool(binding: ToolRef | NativeToolConfig, registry: ToolRegistry) -> Tool:
    if isinstance(binding, ToolRef):
        return registry.create(binding.tool_id, binding.config)
    native_type = NATIVE_TOOLS.get(binding.tool_type)
    if native_type is None:
        # Computer, shell, apply-patch and other stateful native tools need an
        # application-owned factory rather than a serialized executor object.
        return registry.create(binding.tool_type, binding.options)
    unknown = binding.options.keys() - {field.name for field in fields(native_type) if field.init}
    if unknown:
        raise ValueError(f"Unknown native tool options: {sorted(unknown)}")
    tool: Tool = TypeAdapter(native_type).validate_python(binding.options)
    return tool


def agent_as_tool(binding: AgentToolRef, agent: Agent[Any], callbacks: CallbackRegistry) -> Tool:
    options: dict[str, Any] = {
        "tool_name": binding.tool_name,
        "tool_description": binding.tool_description,
        "is_enabled": binding.is_enabled,
        "needs_approval": binding.needs_approval,
        "include_input_schema": binding.include_input_schema,
    }
    callback_fields: dict[str, CallbackKind] = {
        "custom_output_extractor": "output_extractor",
        "is_enabled": "tool_enabled",
        "on_stream": "stream",
        "hooks": "run_hooks",
        "failure_error_function": "tool_error",
        "needs_approval": "tool_approval",
        "parameters": "input_type",
        "input_builder": "input_builder",
    }
    for field, kind in callback_fields.items():
        value = getattr(binding, field)
        if isinstance(value, CallbackRef):
            options[field] = callbacks.resolve(kind, value)
        elif field in binding.model_fields_set:
            options[field] = value
    if binding.max_turns is not None:
        options["max_turns"] = binding.max_turns
    return agent.as_tool(**options)
