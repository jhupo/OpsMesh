"""Serialize accepted turns without reserving a worker while an Agent runs."""

from datetime import timedelta
from functools import partial

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.runtime.temporal.contracts import ExecutionRequest, WorkflowSignal


@workflow.defn(name="opsmesh.conversation")
class ConversationWorkflow:
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
            observed_generation = self._generation
            await workflow.execute_activity(
                "opsmesh.conversation.advance",
                request,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=30)),
            )
            await workflow.wait_condition(partial(self._changed, observed_generation))
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(request)
