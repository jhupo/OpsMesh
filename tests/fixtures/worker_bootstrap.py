"""Deterministic native probe without importing the test harness into replay."""

from datetime import timedelta

from temporalio import activity, workflow


@activity.defn(name="acceptance.bootstrap")
async def bootstrap_probe(value: str) -> str:
    return value


@workflow.defn(name="acceptance.bootstrap")
class BootstrapWorkflow:
    @workflow.run
    async def run(self, value: str) -> str:
        return await workflow.execute_activity(
            bootstrap_probe, value, start_to_close_timeout=timedelta(seconds=10)
        )
