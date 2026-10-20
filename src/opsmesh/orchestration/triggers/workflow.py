"""Native Temporal timers and schedules only; PostgreSQL owns receipts."""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from opsmesh.orchestration.triggers.schemas import SourceEventWork, TriggerWork


@workflow.defn
class TriggerWorkflow:
    @workflow.run
    async def run(self, reference: TriggerWork) -> dict[str, Any]:
        result: dict[str, Any] = await workflow.execute_activity(
            "triggers.sync" if reference.action == "sync" else "triggers.fire",
            {**reference.model_dump(mode="json"), "occurrence": workflow.info().run_id},
            result_type=dict[str, Any],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        return result


@workflow.defn
class SourceEventWorkflow:
    @workflow.run
    async def run(self, reference: SourceEventWork) -> dict[str, Any]:
        total = reference.accepted
        for _ in range(100):
            result: dict[str, Any] = await workflow.execute_activity(
                "triggers.consume",
                reference,
                result_type=dict[str, Any],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            total += result["accepted"]
            if result["after_subscription"] is None:
                return {"accepted": total}
            reference = SourceEventWork.model_validate(
                {
                    **reference.model_dump(mode="json"),
                    "after_subscription": result["after_subscription"],
                    "accepted": total,
                }
            )
        workflow.continue_as_new(reference)
