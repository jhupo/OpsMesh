"""Apply native MCP configuration semantics while preserving the platform tool gateway."""

from copy import deepcopy
from typing import Any

from agents.strict_schema import ensure_strict_json_schema

from opsmesh.framework.agents import AgentSpecification, ToolBinding
from opsmesh.framework.errors import ErrorInfo, OpsMeshError
from opsmesh.modules.runtime.api import MCPToolFailure, RuntimeExtensions


def configure_mcp_tools(
    spec: AgentSpecification, tools: tuple[ToolBinding, ...]
) -> tuple[ToolBinding, ...]:
    configured = []
    for binding in tools:
        if binding.resource_ref not in spec.mcp_refs:
            configured.append(binding)
            continue
        changes: dict[str, Any] = {}
        if not spec.mcp_config.include_server_in_tool_names:
            changes["name"] = binding.source_name or binding.name
            changes["aliases"] = (*binding.aliases, binding.name)
        if spec.mcp_config.tool_filter_ref:
            changes["enabled_ref"] = spec.mcp_config.tool_filter_ref
        if spec.mcp_config.convert_schemas_to_strict:
            try:
                changes["input_schema"] = ensure_strict_json_schema(deepcopy(binding.input_schema))
                changes["strict_schema"] = True
            except Exception:
                # Matches SDK MCP best-effort conversion: preserve the original schema.
                changes.pop("input_schema", None)
                changes["strict_schema"] = False
        configured.append(binding.model_copy(update=changes))
    return tuple(configured)


async def handle_mcp_failure(
    spec: AgentSpecification,
    binding: ToolBinding,
    wrapper: Any,
    error: OpsMeshError,
    extensions: RuntimeExtensions,
) -> str:
    import inspect

    if (
        binding.resource_ref not in spec.mcp_refs
        or spec.mcp_config.failure_behavior != "message"
        or error.code not in {"TOOL_EXECUTION_FAILED", "TOOL_TIMEOUT", "MCP_DISCOVERY_FAILED"}
    ):
        raise error
    if spec.mcp_config.failure_formatter_ref is None:
        return f"MCP tool failed ({error.code})."
    failure = MCPToolFailure(
        server_ref=binding.resource_ref or "",
        tool_name=binding.source_name or binding.name,
        call_id=wrapper.tool_call_id,
        error=ErrorInfo(
            code=error.code, safe_message="MCP tool execution failed", retryable=error.retryable
        ),
    )
    callback = extensions.mcp_failure_formatter(spec.mcp_config.failure_formatter_ref)
    value = callback(wrapper.context, failure)
    value = await value if inspect.isawaitable(value) else value
    if not isinstance(value, str):
        raise OpsMeshError("EXTENSION_INVALID", "MCP error formatter must return text")
    return value
