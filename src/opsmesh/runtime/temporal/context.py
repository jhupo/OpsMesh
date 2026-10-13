"""Bind business references at the Activity boundary; native OTel owns trace context."""

from typing import Any

from temporalio import activity
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.telemetry.request_context import (
    LOG_CONTEXT_FIELDS,
    reset_log_context,
    set_log_context,
)


class ExecutionContextInterceptor(Interceptor):
    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _ExecutionActivityContext(next)


class _ExecutionActivityContext(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        # Clear inherited business fields before binding this Activity's own references.
        values: dict[str, object] = dict.fromkeys(LOG_CONTEXT_FIELDS)
        info = activity.info()
        values.update(
            workflow_id=info.workflow_id,
            workflow_run_id=info.workflow_run_id,
            activity_id=info.activity_id,
            activity_attempt=info.attempt,
        )
        for argument in input.args:
            if isinstance(argument, ExecutionRequest):
                values.update(
                    workspace_id=argument.workspace_id,
                    execution_id=argument.execution_id,
                    request_id=argument.request_id,
                    user_id=argument.requested_by_user_id,
                    run_id=argument.resource_id if argument.kind == ExecutionKind.AGENT else None,
                )
                break
        tokens = set_log_context(**values)
        try:
            return await super().execute_activity(input)
        finally:
            reset_log_context(tokens)
