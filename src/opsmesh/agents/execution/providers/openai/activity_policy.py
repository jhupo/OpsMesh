"""Bound retries and cancellation for model and idempotent product Activities."""

from datetime import timedelta

from temporalio.common import RetryPolicy
from temporalio.openai_agents import ModelActivityParameters
from temporalio.workflow import ActivityCancellationType

from opsmesh.agents.execution.providers.openai.stream_events import MODEL_EVENT_TOPIC
from opsmesh.shared.config import Settings

AGENT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[
        "ResourceAccessDeniedError",
        "AgentExecutionPolicyError",
        "AgentExecutionCancelledError",
        "ModelProviderUnavailableError",
        "CostBudgetExceededError",
        "ValueError",
    ],
)

MODEL_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    maximum_interval=timedelta(seconds=20),
    non_retryable_error_types=[
        "ResourceAccessDeniedError",
        "AgentExecutionPolicyError",
        "AgentExecutionCancelledError",
        "ModelProviderUnavailableError",
        "CostBudgetExceededError",
        "ValueError",
    ],
)


def model_activity_parameters(settings: Settings) -> ModelActivityParameters:
    return ModelActivityParameters(
        start_to_close_timeout=timedelta(seconds=settings.model_request_timeout_seconds),
        streaming_topic=MODEL_EVENT_TOPIC,
        heartbeat_timeout=timedelta(seconds=30),
        retry_policy=MODEL_ACTIVITY_RETRY_POLICY,
        cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
    )
