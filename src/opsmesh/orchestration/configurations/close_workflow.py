"""Durably wait for owner-confirmed configuration cleanup."""

from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy


class ConfigurationCloseWork(BaseModel):
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "ConfigurationCloseWorkflow"
    closure_id: UUID


@workflow.defn
class ConfigurationCloseWorkflow:
    @workflow.run
    async def run(self, work: ConfigurationCloseWork) -> dict[str, Any]:
        while True:
            result = cast(
                dict[str, Any],
                await workflow.execute_activity(
                    "configurations.close",
                    str(work.closure_id),
                    result_type=dict[str, Any],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                ),
            )
            if result["status"] == "completed":
                return result
            await workflow.sleep(timedelta(seconds=5 if result["status"] == "draining" else 30))
