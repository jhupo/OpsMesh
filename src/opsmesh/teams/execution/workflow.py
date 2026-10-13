"""A Team request advances once; subsequent product events create new requests."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest


@workflow.defn(name="opsmesh.team_orchestration")
class TeamOrchestrationWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        if request.kind != ExecutionKind.TEAM_ORCHESTRATION:
            raise ApplicationError(
                "Team workflow requires a Team orchestration reference",
                type="InvalidExecutionKind",
                non_retryable=True,
            )
        try:
            await workflow.execute_activity(
                "opsmesh.team.advance",
                request,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except BaseException:
            await asyncio.shield(
                workflow.execute_activity(
                    "opsmesh.team.record_unknown",
                    request,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            )
            raise
