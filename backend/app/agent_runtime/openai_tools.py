from __future__ import annotations

import json
from typing import Any

from agents import (
    FunctionTool,
    ToolGuardrailFunctionOutput,
    ToolInputGuardrail,
)
from agents.tool_context import ToolContext

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolExecutor,
)


class OpenAIToolBridge:
    def tools(self, request: AgentRunRequest) -> list[Any]:
        if request.tool_executor is None:
            return []
        return [
            self.function_tool(definition, request.tool_executor)
            for definition in request.context.tool_definitions
        ]

    def function_tool(
        self,
        definition: AgentRuntimeToolDefinition,
        executor: AgentRuntimeToolExecutor,
    ) -> Any:
        async def invoke_tool(ctx: ToolContext[Any], raw_arguments: str) -> dict[str, object]:
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError:
                parsed = None
            if not isinstance(parsed, dict):
                return {
                    "error": {
                        "code": "tool_arguments_invalid",
                        "message": "Tool arguments must be a JSON object",
                    },
                    "tool_name": definition.name,
                    "status": "failed",
                }
            result = executor.execute_tool(
                context=ctx.context,
                tool_name=definition.name,
                arguments=parsed,
            )
            if result.status == "completed":
                return tool_response_with_metadata(
                    tool_name=definition.name,
                    status=result.status,
                    payload=result.output or {},
                    metadata=result.metadata,
                )
            return {
                "error": result.error
                or {
                    "code": "mcp_tool_failed",
                    "message": "MCP tool failed",
                },
                "tool_name": definition.name,
                "status": result.status,
                "metadata": result.metadata,
            }

        return FunctionTool(
            name=definition.name,
            description=definition.description,
            params_json_schema=dict(definition.input_schema),
            on_invoke_tool=invoke_tool,
            strict_json_schema=False,
            tool_input_guardrails=[tool_provenance_guardrail(definition.name)],
        )


def safe_tool_function_name(tool_name: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in tool_name)
    return safe or "tool"


def tool_response_with_metadata(
    *,
    tool_name: str,
    status: str,
    payload: dict[str, object],
    metadata: dict[str, object],
) -> dict[str, object]:
    response = dict(payload)
    response.setdefault("tool_name", tool_name)
    response.setdefault("status", status)
    response["metadata"] = metadata
    return response


def tool_provenance_guardrail(tool_name: str) -> ToolInputGuardrail[Any]:
    async def assert_runtime_tool_provenance(data: Any) -> ToolGuardrailFunctionOutput:
        runtime_context = getattr(getattr(data, "context", None), "context", None)
        allowed_tools = runtime_allowed_tools(runtime_context)
        allowed = tool_name in allowed_tools
        return ToolGuardrailFunctionOutput(
            output_info={
                "tool_name": tool_name,
                "guardrail": "runtime_allowed_tool_provenance",
                "allowed": allowed,
            },
            behavior={"type": "allow"}
            if allowed
            else {
                "type": "reject_content",
                "message": f"Tool {tool_name} is not present in runtime context.",
            },
        )

    return ToolInputGuardrail(
        assert_runtime_tool_provenance,
        name=f"{tool_name}:runtime_allowed_tool_provenance",
    )


def runtime_allowed_tools(runtime_context: object) -> tuple[str, ...]:
    raw_allowed_tools: object
    if isinstance(runtime_context, dict):
        raw_allowed_tools = runtime_context.get("allowed_tools", ())
    else:
        raw_allowed_tools = getattr(runtime_context, "allowed_tools", ())
    if not isinstance(raw_allowed_tools, list | tuple):
        return ()
    return tuple(tool for tool in raw_allowed_tools if isinstance(tool, str))
