from __future__ import annotations

import json
from typing import Any

from agents import (
    FunctionTool,
    ToolGuardrailFunctionOutput,
    ToolInputGuardrail,
)
from agents.tool_context import ToolContext

from backend.app.agent_runtime.cancellation import raise_if_cancelled
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeCancellation,
    AgentRuntimeContext,
    AgentRuntimeToolDefinition,
    AgentRuntimeToolExecutor,
)


class OpenAIToolBridge:
    def tools(self, request: AgentRunRequest) -> list[Any]:
        if request.tool_executor is None:
            return []
        return [
            self.function_tool(
                definition,
                request.tool_executor,
                runtime_context=request.context,
                cancellation=request.cancellation,
            )
            for definition in request.context.tool_definitions
        ]

    def function_tool(
        self,
        definition: AgentRuntimeToolDefinition,
        executor: AgentRuntimeToolExecutor,
        *,
        runtime_context: AgentRuntimeContext,
        cancellation: AgentRuntimeCancellation | None = None,
    ) -> Any:
        approval_reviews: dict[str, dict[str, object]] = {}

        async def needs_approval(
            ctx: Any,
            arguments: dict[str, Any],
            call_id: str,
        ) -> bool:
            review = executor.review_tool_call(
                context=runtime_context,
                tool_name=definition.name,
                arguments=arguments,
            )
            if not isinstance(review, dict):
                raise ValueError("Tool approval review must return a mapping")
            approval_reviews[call_id] = dict(review)
            decision = review.get("decision")
            if decision == "deny":
                raise ValueError(f"Tool {definition.name} was denied by policy")
            return decision == "require_approval"

        async def invoke_tool(ctx: ToolContext[Any], raw_arguments: str) -> dict[str, object]:
            await raise_if_cancelled(cancellation)
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
            result = await executor.execute_tool(
                context=runtime_context,
                tool_name=definition.name,
                arguments=parsed,
                tool_call_id=ctx.tool_call_id,
                approval_granted=True,
            )
            await raise_if_cancelled(cancellation)
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

        tool = FunctionTool(
            name=definition.name,
            description=definition.description,
            params_json_schema=dict(definition.input_schema),
            on_invoke_tool=invoke_tool,
            strict_json_schema=False,
            tool_input_guardrails=[
                tool_provenance_guardrail(
                    definition.name,
                    runtime_context=runtime_context,
                )
            ],
            needs_approval=needs_approval,
        )
        tool._opsmesh_tool_kind = definition.source
        tool._opsmesh_approval_reviews = approval_reviews
        return tool


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


def tool_provenance_guardrail(
    tool_name: str,
    *,
    runtime_context: AgentRuntimeContext | None = None,
) -> ToolInputGuardrail[Any]:
    async def assert_runtime_tool_provenance(data: Any) -> ToolGuardrailFunctionOutput:
        active_context = runtime_context or getattr(
            getattr(data, "context", None),
            "context",
            None,
        )
        allowed_tools = runtime_allowed_tools(active_context)
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
