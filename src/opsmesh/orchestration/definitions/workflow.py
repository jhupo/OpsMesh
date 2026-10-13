"""Durable execution of a user-authored non-model orchestration node."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from temporalio.openai_agents.workflow import temporal_mcp_server

    from opsmesh.agents.execution.contracts import AgentExecutionContext, ToolExecutionResult
    from opsmesh.agents.execution.providers.openai.activity_policy import (
        AGENT_ACTIVITY_RETRY_POLICY,
    )
    from opsmesh.agents.execution.providers.openai.tools_temporal import (
        AgentToolOperation,
        TemporalToolExecutor,
    )
    from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest, WorkflowSignal


class NodeExecutionDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_type: str
    status: str
    context: AgentExecutionContext | None = None
    tool_name: str | None = None
    arguments: dict[str, object] = Field(default_factory=dict)
    tool_call_id: str | None = None
    approval_granted: bool = False
    approval_id: UUID | None = None
    invocation_id: UUID | None = None
    result: ToolExecutionResult | None = None


class NodeExecutionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ExecutionRequest
    result: ToolExecutionResult


@workflow.defn(name="opsmesh.orchestration")
class OrchestrationNodeWorkflow:
    def __init__(self) -> None:
        self._decisions: set[UUID] = set()
        self._completed_children: set[UUID] = set()

    @workflow.signal(name="approval")
    def approval(self, signal: WorkflowSignal) -> None:
        self._decisions.add(signal.resource_id)

    @workflow.signal(name="child_completed")
    def child_completed(self, signal: WorkflowSignal) -> None:
        self._completed_children.add(signal.resource_id)

    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        if request.kind != ExecutionKind.ORCHESTRATION:
            raise ValueError("Orchestration node requires an orchestration execution reference")
        try:
            definition = await self._prepare(request)
            while definition is not None:
                if definition.status == "waiting_approval":
                    approval_id = definition.approval_id
                    if approval_id is None:
                        raise ValueError("Waiting node has no persisted approval identity")
                    await self._wait_for_approval(approval_id)
                    self._decisions.remove(approval_id)
                    definition = await self._prepare(request)
                    continue
                if definition.status == "waiting_subworkflow":
                    invocation_id = definition.invocation_id
                    if invocation_id is None:
                        raise ValueError("Waiting node has no persisted child invocation identity")
                    await self._wait_for_child(invocation_id)
                    self._completed_children.remove(invocation_id)
                    definition = await self._prepare(request)
                    continue
                result = definition.result
                if result is None:
                    result = await self._execute(definition)
                await workflow.execute_activity(
                    "opsmesh.orchestration.complete",
                    NodeExecutionOutcome(request=request, result=result),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                )
                if result.status != "waiting_approval":
                    return
                definition = await self._prepare(request)
        except asyncio.CancelledError:
            await asyncio.shield(
                workflow.execute_activity(
                    "opsmesh.orchestration.cancel",
                    request,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                )
            )
            raise
        except Exception as exc:
            if is_cancelled_exception(exc):
                await asyncio.shield(
                    workflow.execute_activity(
                        "opsmesh.orchestration.cancel",
                        request,
                        start_to_close_timeout=timedelta(minutes=1),
                        retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                    )
                )
                raise CancelledError("Orchestration execution was cancelled") from exc
            await workflow.execute_activity(
                "opsmesh.orchestration.fail",
                request,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
            )
            raise

        finally:
            await workflow.wait_condition(workflow.all_handlers_finished)
            await asyncio.shield(
                workflow.execute_activity(
                    "opsmesh.execution.release",
                    request,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
            )

    @staticmethod
    async def _prepare(request: ExecutionRequest) -> NodeExecutionDefinition | None:
        return cast(
            NodeExecutionDefinition | None,
            await workflow.execute_activity(
                "opsmesh.orchestration.prepare",
                request,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                result_type=NodeExecutionDefinition,
            ),
        )

    async def _wait_for_approval(self, approval_id: UUID) -> None:
        await workflow.wait_condition(lambda: approval_id in self._decisions)

    async def _wait_for_child(self, invocation_id: UUID) -> None:
        await workflow.wait_condition(lambda: invocation_id in self._completed_children)

    @staticmethod
    async def _execute(definition: NodeExecutionDefinition) -> ToolExecutionResult:
        context = definition.context
        name = definition.tool_name
        if context is None or name is None or definition.tool_call_id is None:
            raise ValueError("Executable tool node has no authorized tool definition")
        if definition.node_type == "tool":
            # An unknown post-commit result must not re-execute a mutating tool.
            return cast(
                ToolExecutionResult,
                await workflow.execute_activity(
                    "opsmesh.agent.execute_tool",
                    AgentToolOperation(
                        context=context,
                        tool_name=name,
                        arguments=definition.arguments,
                        tool_call_id=definition.tool_call_id,
                        approval_granted=definition.approval_granted,
                    ),
                    start_to_close_timeout=timedelta(hours=8),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                    result_type=ToolExecutionResult,
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                ),
            )
        if definition.node_type != "mcp":
            raise ValueError("Unknown executable orchestration node type")
        tool = next(item for item in context.tool_definitions if item.name == name)
        if tool.mcp_server_id is None:
            raise ValueError("MCP node has no authorized server reference")
        profile_id = context.metadata.get("agent_profile_id")
        fingerprint = context.metadata.get("capability_catalog_fingerprint")
        if not isinstance(profile_id, str) or not isinstance(fingerprint, str):
            raise ValueError("MCP node has no frozen Agent capability reference")
        # The factory reauthorizes this persisted run and frozen tool manifest.
        server = temporal_mcp_server(
            "opsmesh",
            factory_argument={
                "workspace_id": str(context.workspace_id),
                "run_id": str(context.run_id),
                "server_id": str(tool.mcp_server_id),
                "agent_profile_id": profile_id,
                "capability_catalog_fingerprint": fingerprint,
            },
            activity_config={
                "start_to_close_timeout": timedelta(hours=8),
                "retry_policy": RetryPolicy(maximum_attempts=1),
                "cancellation_type": workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            },
            failure_error_function=None,
        )
        await TemporalToolExecutor().review_tool_call(
            context=context,
            tool_name=name,
            arguments=definition.arguments,
        )
        result = await server.call_tool(
            name,
            cast(dict[str, Any], definition.arguments),
            meta={"opsmesh_tool_call_id": definition.tool_call_id},
        )
        return ToolExecutionResult(
            status="failed" if result.is_error else "completed",
            output=result.model_dump(mode="json"),
            error={"code": "mcp_tool_failed", "message": "MCP tool returned an error"}
            if result.is_error
            else None,
        )
