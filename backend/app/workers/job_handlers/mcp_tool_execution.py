import asyncio

from backend.app.capabilities.execution import McpToolExecutionService
from backend.app.capabilities.mcp_adapter_resolver import McpAdapterResolver
from backend.app.capabilities.mcp_execution_types import McpExecutionRequest
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.job_routing import optional_uuid, required_string
from backend.app.workers.jobs import JobPayload
from backend.app.workers.routing_payloads import dict_payload, string_tuple


class McpToolExecutionJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        payload = job.routing
        tool_name = required_string(payload, "tool_name", context="MCP tool execution job")
        arguments = dict_payload(payload.get("arguments"), context="MCP tool execution job")
        runtime_allowed_tools = string_tuple(
            payload.get("runtime_allowed_tools"),
            context="MCP tool execution",
        )
        server_id = optional_uuid(
            payload.get("mcp_server_id"),
            context="MCP tool execution",
        )
        agent_run_id = (
            optional_uuid(payload.get("agent_run_id"), context="MCP tool execution")
            or job.resource_id
        )

        asyncio.run(
            McpToolExecutionService(
                self._context.session,
                self._context.mcp_adapter or McpAdapterResolver(),
                settings=self._context.settings,
            ).execute(
                McpExecutionRequest(
                    workspace_id=job.workspace_id,
                    agent_run_id=agent_run_id,
                    mcp_server_id=server_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    runtime_allowed_tools=runtime_allowed_tools,
                )
            )
        )
        self._context.session.commit()
