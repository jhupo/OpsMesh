import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    is_cancelled_exception,
)

with workflow.unsafe.imports_passed_through():
    from opsmesh.runtime.temporal.contracts import ExecutionRequest


@workflow.defn(name="opsmesh.plugin_download")
class PluginDownloadWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        try:
            await workflow.execute_activity(
                "opsmesh.plugin.download",
                request,
                start_to_close_timeout=timedelta(minutes=2),
                heartbeat_timeout=timedelta(seconds=45),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=10),
                ),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except (ActivityError, asyncio.CancelledError) as exc:
            cancelled = is_cancelled_exception(exc)
            code = "download_cancelled" if cancelled else "download_failed"
            if isinstance(exc, ActivityError) and isinstance(exc.cause, ApplicationError):
                code = exc.cause.type or code
            await asyncio.shield(
                workflow.execute_activity(
                    "opsmesh.plugin.download_failed",
                    args=[request, code],
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            )
            if cancelled:
                raise CancelledError("Plugin download cancelled") from exc
            raise
