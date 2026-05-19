from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRuntimeContext,
    AgentRuntimeToolResult,
)
from backend.app.capabilities.execution import (
    McpExecutionRequest,
    McpToolAdapter,
    McpToolAdapterResolver,
    McpToolExecutionService,
)


class BackendToolExecutor:
    def __init__(self, mcp_execution_service: McpToolExecutionService) -> None:
        self._mcp_execution_service = mcp_execution_service

    @classmethod
    def for_mcp_adapter(
        cls,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
    ) -> BackendToolExecutor:
        return cls(McpToolExecutionService(session, adapter))

    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        result = self._mcp_execution_service.execute(
            McpExecutionRequest(
                workspace_id=context.workspace_id,
                agent_run_id=context.run_id,
                tool_name=tool_name,
                arguments=arguments,
                runtime_allowed_tools=context.allowed_tools,
            )
        )
        return AgentRuntimeToolResult(
            status=result.status,
            output=result.response,
            error=result.error,
        )


class DisabledToolExecutor:
    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        return AgentRuntimeToolResult(
            status="failed",
            error={
                "code": "tool_executor_unavailable",
                "message": f"Tool executor is not configured for {tool_name}",
            },
        )
