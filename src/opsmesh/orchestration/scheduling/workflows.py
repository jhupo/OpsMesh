from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.orchestration.scheduling.contracts import ScheduleOccurrence
    from opsmesh.runtime.temporal.contracts import ExecutionRequest


@workflow.defn(name="opsmesh.schedule_control")
class ScheduleControlWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.schedule.synchronize",
            request,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=10),
        )


@workflow.defn(name="opsmesh.schedule_tick")
class ScheduleTickWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        occurrence = ScheduleOccurrence(
            request=request,
            occurrence_id=workflow.info().workflow_id,
            started_at=workflow.now(),
        )
        while not await workflow.execute_activity(
            "opsmesh.schedule.materialize",
            occurrence,
            result_type=bool,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        ):
            # A native timer retains the scheduled occurrence without occupying
            # an Activity slot or losing a one-shot event during an update.
            await workflow.sleep(timedelta(seconds=30))
