from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.runtime.temporal.contracts import ExecutionRequest


@workflow.defn(name="opsmesh.sandbox_control")
class SandboxControlWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.sandbox.control",
            request,
            start_to_close_timeout=timedelta(minutes=35),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


@workflow.defn(name="opsmesh.sandbox_cleanup")
class SandboxCleanupWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.sandbox.cleanup",
            request,
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


WORKFLOWS = (SandboxControlWorkflow, SandboxCleanupWorkflow)
