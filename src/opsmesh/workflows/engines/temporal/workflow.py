"""Temporal wrapper for the engine-neutral graph and its durable product records."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import asyncio
    from typing import Any
    from uuid import UUID

    from pydantic import BaseModel, ConfigDict
    from temporalio.exceptions import ApplicationError

    from opsmesh.workflows.context import WorkflowContext
    from opsmesh.workflows.contracts import PreparedWorkflow
    from opsmesh.workflows.engines.temporal.agent_bindings import ACTIVITY_OPTIONS
    from opsmesh.workflows.engines.temporal.runtime import TemporalWorkflowRuntime
    from opsmesh.workflows.runner import GraphRunner


class WorkflowCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "WorkflowExecutionWorkflow"


@workflow.defn(name="WorkflowExecutionWorkflow")
class WorkflowExecutionWorkflow:
    def __init__(self) -> None:
        self.wake_generation = 0

    @workflow.signal
    async def wake(self, dispatch_id: str, signal_id: str) -> None:
        if dispatch_id != signal_id:
            raise ValueError("Signal identity differs from its delivery receipt")
        # Notifications only trigger a durable-state read; duplicate delivery is harmless.
        self.wake_generation += 1

    @workflow.run
    async def run(self, command: WorkflowCommand) -> object:
        run_id = command.execution_id
        if (
            workflow.info().workflow_id != f"workflow:{run_id}"
            or command.workflow_id != workflow.info().workflow_id
        ):
            raise ValueError("Workflow identity does not match its command")
        completion: dict[str, object] = {"run_id": str(run_id), "status": "failed"}
        try:
            admitted: dict[str, Any] = await workflow.execute_activity(
                "workflows.admit",
                {"run_id": str(run_id)},
                result_type=dict[str, Any],
                **ACTIVITY_OPTIONS,
            )
            if admitted.get("cancelled"):
                completion["status"] = "cancelled"
                return None
            prepared = PreparedWorkflow.model_validate(admitted["prepared"])
            runtime = TemporalWorkflowRuntime(run_id, prepared, wake=lambda: self.wake_generation)
            result = await GraphRunner(WorkflowContext(run_id, prepared, runtime)).run(
                admitted["input"]
            )
            completion.update(status="completed", output=result)
            return result
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                completion["status"] = "cancelled"
                raise
            raise ApplicationError(
                "Workflow graph failed", type="WorkflowGraphFailed", non_retryable=True
            ) from error
        finally:
            await asyncio.shield(
                workflow.execute_activity(
                    "workflows.finish", completion, result_type=str, **ACTIVITY_OPTIONS
                )
            )
