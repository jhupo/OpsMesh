from datetime import timedelta
from functools import partial

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from opsmesh.orchestration.automations.execution_contracts import ReplyPreparation
    from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest, WorkflowSignal


@workflow.defn(name="opsmesh.entry_event")
class AutomationEventWorkflow:
    def __init__(self) -> None:
        self._generation = 0

    @workflow.signal
    def wake(self, signal: WorkflowSignal) -> None:
        self._generation += 1

    def _changed(self, generation: int) -> bool:
        return self._generation != generation

    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        while True:
            generation = self._generation
            status = await workflow.execute_activity(
                "opsmesh.automation.dispatch",
                request,
                result_type=str,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if status != "pending":
                return
            await workflow.wait_condition(partial(self._changed, generation))


@workflow.defn(name="opsmesh.reply_delivery")
class ReplyDeliveryWorkflow:
    def __init__(self) -> None:
        self._generation = 0

    @workflow.signal
    def wake(self, signal: WorkflowSignal) -> None:
        self._generation += 1

    def _changed(self, generation: int) -> bool:
        return self._generation != generation

    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        while True:
            generation = self._generation
            preparation = await workflow.execute_activity(
                "opsmesh.automation.prepare_reply",
                request,
                result_type=ReplyPreparation,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if preparation.complete:
                return
            if preparation.delivery_id is not None:
                delivery = ExecutionRequest(
                    execution_id=preparation.delivery_id,
                    workspace_id=request.workspace_id,
                    resource_id=preparation.delivery_id,
                    kind=ExecutionKind.REPLY_DELIVERY,
                )
                try:
                    status = await workflow.execute_activity(
                        "opsmesh.channel.send",
                        delivery,
                        result_type=str,
                        start_to_close_timeout=timedelta(seconds=45),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                except ActivityError:
                    status = await workflow.execute_activity(
                        "opsmesh.channel.interrupted",
                        delivery,
                        result_type=str,
                        start_to_close_timeout=timedelta(minutes=1),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                if status in {"sending", "unknown"}:
                    # An uncertain external write is resolved only by new evidence.
                    # Reading its stable delivery again never resends the message.
                    await workflow.wait_condition(partial(self._changed, generation))
                    continue
                complete = await workflow.execute_activity(
                    "opsmesh.automation.settle_reply",
                    request,
                    result_type=bool,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                if complete:
                    return
                # Re-read once after network delivery because task state may have
                # progressed while the reply was in flight.
                continue
            await workflow.wait_condition(partial(self._changed, generation))
