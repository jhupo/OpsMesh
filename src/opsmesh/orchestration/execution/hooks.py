"""Deterministic extension hook callbacks delegated to registered Activities."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from temporalio import workflow
from temporalio.common import RetryPolicy


class HookOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID
    occurrence: str
    stage: str
    payload: dict[str, Any]
    entered: list[str] | None = None


async def workflow_hooks(occurrence: HookOccurrence) -> dict[str, Any]:
    result = await workflow.execute_activity(
        "orchestration.hooks",
        occurrence,
        result_type=dict[str, Any],
        start_to_close_timeout=timedelta(minutes=5),
        retry_policy=RetryPolicy(maximum_attempts=1),
        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
    )
    return dict(result)
