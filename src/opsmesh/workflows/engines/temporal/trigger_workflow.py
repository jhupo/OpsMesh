"""Native Schedules dispatch ordinary runs through idempotent Activities."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import asyncio
    from datetime import timedelta
    from typing import Any

    from temporalio.common import RetryPolicy


@workflow.defn(name="TriggerWorkflow")
class TriggerWorkflow:
    @workflow.run
    async def run(self, command: dict[str, object]) -> object:
        options: dict[str, Any] = {
            "start_to_close_timeout": timedelta(seconds=30),
            "retry_policy": RetryPolicy(maximum_interval=timedelta(seconds=30)),
        }
        payload = {**command, "event_id": workflow.info().workflow_id}
        delivery: dict[str, object] = await workflow.execute_activity(
            "trigger.fire", payload, result_type=dict, **options
        )
        if delivery.get("skipped"):
            return delivery
        try:
            for _ in range(500):
                result: dict[str, object] = await workflow.execute_activity(
                    "trigger.result", delivery, result_type=dict, **options
                )
                if result["status"] in {"completed", "failed", "cancelled"}:
                    return result
                await workflow.sleep(10)
            # Keep the Schedule overlap policy effective while the target is still running.
            workflow.continue_as_new(command)
        except asyncio.CancelledError:
            await asyncio.shield(workflow.execute_activity("trigger.cancel", delivery, **options))
            raise
