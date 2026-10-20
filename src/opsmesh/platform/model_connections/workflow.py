"""Persist one administrator-requested read-only upstream probe."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.platform.model_connections.activities import ModelProviderActivities, ProbeOutcome
    from opsmesh.platform.model_connections.service import (
        MODEL_PROVIDER_PROBE_WORKFLOW,
        ModelProviderProbeExecution,
    )


@workflow.defn(name=MODEL_PROVIDER_PROBE_WORKFLOW)
class ModelProviderProbeWorkflow:
    @workflow.run
    async def run(self, command: ModelProviderProbeExecution) -> ProbeOutcome:
        return await workflow.execute_activity_method(
            ModelProviderActivities.probe_connection,
            command,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
