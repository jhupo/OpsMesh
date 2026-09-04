from __future__ import annotations

from typing import Any

from agents import (
    RunContextWrapper,
    ToolGuardrailFunctionOutput,
    ToolInputGuardrail,
    function_tool,
)

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeToolExecutor,
)


class OpenAIToolBridge:
    def tools(self, request: AgentRunRequest) -> list[Any]:
        if request.tool_executor is None:
            return []
        return [
            self.mcp_function_tool(tool_name, request.tool_executor)
            for tool_name in request.context.allowed_tools
        ]

    def mcp_function_tool(
        self,
        tool_name: str,
        executor: AgentRuntimeToolExecutor,
    ) -> Any:
        def call_mcp_tool(
            ctx: RunContextWrapper[Any],
            arguments: dict[str, object],
        ) -> dict[str, object]:
            result = executor.execute_tool(
                context=ctx.context,
                tool_name=tool_name,
                arguments=arguments,
            )
            if result.status == "completed":
                return tool_response_with_metadata(
                    tool_name=tool_name,
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
                "tool_name": tool_name,
                "status": result.status,
                "metadata": result.metadata,
            }

        call_mcp_tool.__name__ = f"mcp_{safe_tool_function_name(tool_name)}"
        call_mcp_tool.__doc__ = (
            "Execute an approved MCP tool. "
            "Pass a JSON object with the arguments required by the tool."
        )
        return function_tool(
            call_mcp_tool,
            name_override=tool_name,
            description_override=f"Execute the approved MCP tool `{tool_name}`.",
            strict_mode=False,
            tool_input_guardrails=[tool_provenance_guardrail(tool_name)],
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
