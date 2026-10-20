"""Durable paginated administrator commands; business I/O is in Activities."""

from datetime import timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

PLATFORM_OPERATION_WORKFLOW = "opsmesh.platform.operation"


class PlatformOperationExecution(BaseModel):
    execution_id: UUID
    workflow_id: str
    workflow_name: Literal["opsmesh.platform.operation"] = "opsmesh.platform.operation"
    generation: int = 1


@workflow.defn(name=PLATFORM_OPERATION_WORKFLOW)
class PlatformOperationWorkflow:
    @workflow.run
    async def run(self, request: PlatformOperationExecution) -> str:
        for _ in range(100):
            try:
                status: str = await workflow.execute_activity(
                    "platform.operation_page",
                    args=[str(request.execution_id), request.generation],
                    result_type=str,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )
            except ActivityError:
                failed_status: str = await workflow.execute_activity(
                    "platform.operation_failed",
                    args=[str(request.execution_id), request.generation],
                    result_type=str,
                    start_to_close_timeout=timedelta(seconds=30),
                )
                return failed_status
            if status != "running":
                return status
        workflow.continue_as_new(request)
