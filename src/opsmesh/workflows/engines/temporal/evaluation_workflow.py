"""Bounded evaluation fan-out; all effects remain in Activities."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import asyncio
    from datetime import timedelta
    from typing import Any
    from uuid import UUID

    from pydantic import BaseModel, ConfigDict
    from temporalio.common import RetryPolicy


class EvaluationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "EvaluationWorkflow"


@workflow.defn(name="EvaluationWorkflow")
class EvaluationWorkflow:
    @workflow.run
    async def run(self, command: EvaluationCommand) -> object:
        if (
            workflow.info().workflow_id != f"evaluation:{command.execution_id}"
            or command.workflow_id != workflow.info().workflow_id
        ):
            raise ValueError("Evaluation Workflow identity does not match its command")
        options: dict[str, Any] = {
            "start_to_close_timeout": timedelta(seconds=30),
            "retry_policy": RetryPolicy(maximum_interval=timedelta(seconds=30)),
        }
        for _ in range(200):
            state: dict[str, Any] = await workflow.execute_activity(
                "evaluation.plan", str(command.execution_id), result_type=dict, **options
            )
            if state["status"] in {"completed", "cancelled", "failed"}:
                return state
            await asyncio.gather(
                *[
                    workflow.execute_activity("evaluation.step", identifier, **options)
                    for identifier in state["cases"]
                ]
            )
            await workflow.sleep(5)
        workflow.continue_as_new(command)
