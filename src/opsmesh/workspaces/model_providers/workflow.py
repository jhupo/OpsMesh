"""Durable model provider probe: one connection test, honestly reported."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    # The activity entry description and its payloads are passed through
    # instead of being reloaded into every Workflow sandbox.
    from opsmesh.workspaces.model_providers.activities import (
        PROBE_NEEDS_ATTENTION,
        PROBE_OK,
        ModelProviderActivities,
        ProbeOutcome,
    )
    from opsmesh.workspaces.model_providers.service import (
        MODEL_PROVIDER_PROBE_WORKFLOW,
        ModelProviderProbeExecution,
    )

PROBE_START_TO_CLOSE_TIMEOUT = timedelta(seconds=60)
PROBE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=3,
)


@workflow.defn(name=MODEL_PROVIDER_PROBE_WORKFLOW)
class ModelProviderProbeWorkflow:
    """Probe one connection; an unconfirmable outcome is never reported as ok."""

    @workflow.run
    async def run(self, command: ModelProviderProbeExecution) -> str:
        outcome: ProbeOutcome = await workflow.execute_activity_method(
            ModelProviderActivities.probe_connection,
            command.connection_id,
            start_to_close_timeout=PROBE_START_TO_CLOSE_TIMEOUT,
            retry_policy=PROBE_RETRY_POLICY,
        )
        if outcome.outcome_unknown:
            return PROBE_NEEDS_ATTENTION
        return PROBE_OK if outcome.reachable else outcome.reason
