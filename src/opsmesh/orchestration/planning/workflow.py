from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest


@workflow.defn(name="opsmesh.task_plan")
class TaskPlanWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        if request.kind != ExecutionKind.TASK_PLAN:
            raise ValueError("TaskPlanWorkflow requires a task-plan execution")
        await workflow.execute_activity(
            "opsmesh.task_plan",
            request,
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
