"""Authorized product tools use Temporal Activities, never Workflow I/O."""

from datetime import timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from opsmesh.agents.execution.contracts import AgentExecutionContext, ToolExecutionResult
from opsmesh.agents.execution.providers.openai.activity_policy import AGENT_ACTIVITY_RETRY_POLICY


class AgentToolOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context: AgentExecutionContext
    tool_name: str | None = None
    arguments: dict[str, object] = Field(default_factory=dict)
    tool_call_id: str | None = None
    approval_granted: bool = False


class TemporalToolExecutor:
    async def authorize_agent(self, *, context: AgentExecutionContext) -> None:
        await workflow.execute_activity(
            "opsmesh.agent.authorize",
            AgentToolOperation(context=context),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
        )

    async def cancel_active_tools(self, *, context: AgentExecutionContext) -> None:
        await workflow.execute_activity(
            "opsmesh.agent.cancel_tools",
            AgentToolOperation(context=context),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
        )

    async def review_tool_call(
        self,
        *,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        return cast(
            dict[str, object],
            await workflow.execute_activity(
                "opsmesh.agent.review_tool",
                AgentToolOperation(context=context, tool_name=tool_name, arguments=arguments),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
            ),
        )

    async def execute_tool(
        self,
        *,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
    ) -> ToolExecutionResult:
        return cast(
            ToolExecutionResult,
            await workflow.execute_activity(
                "opsmesh.agent.execute_tool",
                AgentToolOperation(
                    context=context,
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_call_id=tool_call_id,
                    approval_granted=approval_granted,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                # These product operations may commit effects before returning.
                # Temporal retains completed results; an unknown outcome must
                # never implicitly replay a mutation.
                retry_policy=RetryPolicy(maximum_attempts=1),
                result_type=ToolExecutionResult,
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            ),
        )
