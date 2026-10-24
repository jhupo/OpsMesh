"""Temporal runtime uses bounded Activities, durable timers and owned child Workflows."""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError as TemporalCancelledError

from opsmesh.workflows.context import WorkflowRuntime
from opsmesh.workflows.contracts import (
    AgentBinding,
    NodeFailure,
    PreparedWorkflow,
    ToolBinding,
    UncertainEffects,
)
from opsmesh.workflows.engines.temporal.agent_bindings import ACTIVITY_OPTIONS
from opsmesh.workflows.engines.temporal.agent_workflow import AgentInstanceWorkflow


class TemporalWorkflowRuntime(WorkflowRuntime):
    def __init__(
        self, run_id: UUID, prepared: PreparedWorkflow, *, wake: Callable[[], int]
    ) -> None:
        self.run_id, self.prepared = run_id, prepared
        self.wake = wake

    async def wait_for_wake(self, generation: int, seconds: float | None = None) -> None:
        try:
            await workflow.wait_condition(
                lambda: self.wake() != generation,
                timeout=timedelta(seconds=seconds) if seconds is not None else None,
            )
        except TimeoutError:
            return

    async def call(self, name: str, data: dict[str, Any]) -> Any:
        return await workflow.execute_activity(
            name, {"run_id": str(self.run_id), **data}, result_type=Any, **ACTIVITY_OPTIONS
        )

    async def begin_node(self, path: str, kind: str, arguments: dict[str, object]) -> None:
        await self.call(
            "workflows.node_begin", {"path": path, "kind": kind, "arguments": arguments}
        )

    async def finish_node(self, path: str, status: str, output: object = None) -> None:
        await self.call("workflows.node_finish", {"path": path, "status": status, "output": output})

    async def agent(self, path: str, binding: AgentBinding, arguments: dict[str, object]) -> object:
        command = await self.call("workflows.agent_submit", {"path": path, "input": arguments})
        try:
            await workflow.execute_child_workflow(
                AgentInstanceWorkflow.run,
                command,
                id=f"instance:{command['execution_id']}",
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except BaseException:
            await asyncio.shield(self.call("workflows.agent_cancel", {"path": path}))
            raise
        result = await self.call("workflows.agent_result", {"path": path})
        if result["status"] == "completed":
            return result["output"]
        if result["status"] in {
            "needs_attention",
            "running",
            "cancelling",
            "queued",
            "interrupted",
        }:
            raise UncertainEffects("The linked Agent run has no confirmed terminal result")
        raise NodeFailure("The linked Agent run failed or was cancelled")

    async def tool(
        self, path: str, binding: ToolBinding, operation: str, arguments: dict[str, object]
    ) -> object:
        while True:
            generation = self.wake()
            state = await self.call(
                "workflows.tool_prepare", {"path": path, "arguments": arguments}
            )
            if state["denied"]:
                raise NodeFailure("The workflow tool was denied")
            if state["approved"]:
                break
            await self.wait_for_wake(generation)
        try:
            return await self.call("workflows.tool", {"path": path, "arguments": arguments})
        except Exception as error:
            raise UncertainEffects("The workflow tool did not return a confirmed result") from error

    async def sleep(self, seconds: float) -> None:
        await workflow.sleep(seconds)

    async def signal(self, path: str, name: str, timeout: float) -> object:
        deadline = workflow.now() + timedelta(seconds=timeout)
        while True:
            generation = self.wake()
            value = await self.call("workflows.signal_poll", {"path": path, "name": name})
            if value["ready"]:
                return value.get("value")
            if workflow.now() >= deadline:
                raise NodeFailure("The addressed workflow signal timed out")
            await self.wait_for_wake(generation, (deadline - workflow.now()).total_seconds())

    async def wait_tasks(self, tasks: list[asyncio.Task[Any]]) -> None:
        await workflow.wait_condition(lambda: any(task.done() for task in tasks))

    def is_cancelled(self, error: BaseException) -> bool:
        while error.__cause__ is not None:
            error = error.__cause__
        return isinstance(error, asyncio.CancelledError | TemporalCancelledError)
