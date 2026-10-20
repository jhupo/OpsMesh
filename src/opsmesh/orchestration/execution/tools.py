"""Dynamic sandbox tool schemas use public FunctionTool callbacks to delegate I/O."""

import asyncio
import json
from datetime import timedelta
from typing import Any

from agents import FunctionTool
from agents.tool_context import ToolContext
from temporalio import workflow
from temporalio.common import RetryPolicy

from opsmesh.orchestration.execution.contracts import PreparedRun, ToolConfiguration, ToolInvocation


def sandbox_tool(
    prepared: PreparedRun,
    agent_key: str,
    definition: ToolConfiguration,
    pending_tools: set[asyncio.Task[Any]],
) -> FunctionTool:
    async def invoke(context: ToolContext[Any], arguments: str) -> Any:
        task = asyncio.current_task()
        assert task is not None
        pending_tools.add(task)
        task.add_done_callback(pending_tools.discard)
        return await workflow.execute_activity(
            "orchestration.tool",
            ToolInvocation(
                run_id=prepared.reference.run_id,
                agent_key=agent_key,
                tool_name=definition.name,
                call_id=context.tool_call_id,
                arguments=json.loads(arguments),
            ),
            start_to_close_timeout=timedelta(minutes=5),
            heartbeat_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    return FunctionTool(
        name=definition.name,
        description=definition.description,
        params_json_schema=definition.input_schema,
        strict_json_schema=False,
        needs_approval=definition.approval_required,
        on_invoke_tool=invoke,
    )
