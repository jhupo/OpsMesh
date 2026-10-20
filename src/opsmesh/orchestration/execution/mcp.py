"""Bind already authorized existing MCP connections to the official Temporal adapter."""

from datetime import timedelta

from agents.mcp import MCPServer
from temporalio.common import RetryPolicy
from temporalio.openai_agents.workflow import temporal_mcp_server
from temporalio.workflow import ActivityCancellationType

from opsmesh.orchestration.execution.contracts import MCPBinding, PreparedRun


def native_mcp_servers(
    prepared: PreparedRun, key: str, bindings: list[MCPBinding]
) -> list[MCPServer]:
    return [
        temporal_mcp_server(
            "opsmesh",
            factory_argument={
                "connection_id": str(binding.connection_id),
                "tools_digest": binding.tools_digest,
                "allowed_tools": binding.allowed_tools,
                "principal_id": str(prepared.principal_id),
                "run_id": str(prepared.reference.run_id),
                "agent_key": key,
            },
            require_approval="always",
            tool_filter={"allowed_tool_names": binding.allowed_tools},
            failure_error_function=None,
            activity_config={
                "start_to_close_timeout": timedelta(minutes=5),
                "heartbeat_timeout": timedelta(seconds=10),
                "retry_policy": RetryPolicy(maximum_attempts=1),
                "cancellation_type": ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            },
        )
        for binding in bindings
    ]
