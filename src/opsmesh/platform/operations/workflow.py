from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.runtime.temporal.contracts import ExecutionRequest


@workflow.defn(name="opsmesh.admin_operation")
class AdminOperationWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.platform.execute_operation",
            request,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
