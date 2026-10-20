"""One model operation; retry reads its receipt and never repeats unknown I/O."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.orchestration.runs.model_operations import ModelOperationWork


@workflow.defn(name="ExecutionModelOperationWorkflow")
class ModelOperationWorkflow:
    @workflow.run
    async def run(self, work: ModelOperationWork) -> None:
        await workflow.execute_activity(
            "orchestration.model_operation",
            work,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
