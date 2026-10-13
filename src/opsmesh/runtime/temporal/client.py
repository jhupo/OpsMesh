from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta

from opentelemetry import context, propagate
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.contrib.opentelemetry import OpenTelemetryPlugin
from temporalio.converter import DefaultFailureConverterWithEncodedAttributes
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.plugin import SimplePlugin
from temporalio.service import RPCError, RPCStatusCode

from opsmesh.runtime.temporal.codec import EncryptedPayloadCodec
from opsmesh.runtime.temporal.context import ExecutionContextInterceptor
from opsmesh.runtime.temporal.contracts import (
    DispatchOperation,
    DispatchRequest,
    ExecutionKind,
    WorkflowSignal,
)
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.shared.config import Settings
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.telemetry.request_context import reset_log_context, set_log_context
from opsmesh.shared.telemetry.tracing import configure_tracer_provider

WORKFLOW_NAMES: dict[ExecutionKind, str] = {
    ExecutionKind.CONVERSATION: "opsmesh.conversation",
    ExecutionKind.AGENT: "opsmesh.agent",
    ExecutionKind.ORCHESTRATION: "opsmesh.orchestration",
    ExecutionKind.TEAM_ORCHESTRATION: "opsmesh.team_orchestration",
    ExecutionKind.TASK_PLAN: "opsmesh.task_plan",
    ExecutionKind.MCP_CONTROL: "opsmesh.mcp_control",
    ExecutionKind.PLUGIN_DOWNLOAD: "opsmesh.plugin_download",
    ExecutionKind.SANDBOX_CONTROL: "opsmesh.sandbox_control",
    ExecutionKind.SANDBOX_CLEANUP: "opsmesh.sandbox_cleanup",
    ExecutionKind.ARCHIVE_EXPORT: "opsmesh.archive_export",
    ExecutionKind.ARCHIVE_VERIFY: "opsmesh.archive_verify",
    ExecutionKind.ARCHIVE_RESTORE_DRILL: "opsmesh.archive_restore_drill",
    ExecutionKind.MEMORY_INDEX: "opsmesh.memory_index",
    ExecutionKind.MEMORY_EMBED: "opsmesh.memory_embed",
    ExecutionKind.KNOWLEDGE_INGEST: "opsmesh.knowledge_ingest",
    ExecutionKind.WEBHOOK_DELIVERY: "opsmesh.webhook_delivery",
    ExecutionKind.SECRET_REENCRYPT: "opsmesh.secret_reencrypt",
    ExecutionKind.MODEL_PROVIDER_HEALTH: "opsmesh.model_provider_health",
    ExecutionKind.AUDIT_INTEGRITY: "opsmesh.audit_integrity",
    ExecutionKind.ADMIN_OPERATION: "opsmesh.admin_operation",
    ExecutionKind.ENTRY_EVENT: "opsmesh.entry_event",
    ExecutionKind.REPLY_DELIVERY: "opsmesh.reply_delivery",
    ExecutionKind.SCHEDULE_CONTROL: "opsmesh.schedule_control",
    ExecutionKind.SCHEDULE_TICK: "opsmesh.schedule_tick",
}


async def connect_temporal(settings: Settings) -> Client:
    configure_tracer_provider(settings)
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        tls=settings.temporal_tls_enabled,
        api_key=settings.temporal_api_key,
        plugins=[
            OpenTelemetryPlugin(add_temporal_spans=True),
            SimplePlugin("OpsMeshExecutionContext", interceptors=[ExecutionContextInterceptor()]),
        ],
        data_converter=replace(
            sdk_data_converter,
            failure_converter_class=DefaultFailureConverterWithEncodedAttributes,
            payload_codec=EncryptedPayloadCodec(
                SecretEncryptionService(
                    secret=settings.credential_encryption_secret,
                    key_id=settings.credential_encryption_key_id,
                    previous_secrets=settings.credential_encryption_previous_secrets,
                )
            ),
        ),
    )


class TemporalExecutionClient:
    """Deliver committed execution commands through the native Temporal client."""

    def __init__(
        self,
        client: Client,
        *,
        task_queue: str,
        workflows: Mapping[ExecutionKind, str] = WORKFLOW_NAMES,
        rpc_timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        self._client = client
        self._task_queue = task_queue
        self._workflows = workflows
        self._rpc_timeout = rpc_timeout

    async def dispatch(self, request: DispatchRequest) -> None:
        # Restore the submitting request's parent after the PostgreSQL/queue boundary.
        carrier = {"traceparent": request.traceparent} if request.traceparent is not None else {}
        if request.tracestate is not None:
            carrier["tracestate"] = request.tracestate
        token = (
            context.attach(propagate.extract(carrier))
            if request.traceparent is not None
            else context.attach(context.Context())
        )
        execution = request.execution
        log_tokens = set_log_context(
            workspace_id=request.workspace_id,
            operation_id=request.dispatch_id,
            request_id=execution.request_id if execution else None,
            execution_id=execution.execution_id if execution else None,
        )
        try:
            await self._dispatch(request)
        finally:
            reset_log_context(log_tokens)
            context.detach(token)

    async def _dispatch(self, request: DispatchRequest) -> None:
        if request.operation == DispatchOperation.START:
            execution = request.execution
            if execution is None:
                raise ValueError("Start dispatch has no execution reference")
            # Stable Workflow IDs make a delivery retry safe after a post-start crash.
            try:
                await self._client.start_workflow(
                    self._workflows[execution.kind],
                    execution,
                    id=request.workflow_id,
                    task_queue=self._task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
                    if execution.kind == ExecutionKind.CONVERSATION
                    else WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING
                    if execution.kind == ExecutionKind.CONVERSATION
                    else WorkflowIDConflictPolicy.FAIL,
                    start_signal="wake" if execution.kind == ExecutionKind.CONVERSATION else None,
                    start_signal_args=(
                        [WorkflowSignal(resource_id=execution.resource_id)]
                        if execution.kind == ExecutionKind.CONVERSATION
                        else []
                    ),
                    rpc_timeout=self._rpc_timeout,
                )
            except WorkflowAlreadyStartedError:
                if execution.kind == ExecutionKind.CONVERSATION:
                    raise
            return
        handle = self._client.get_workflow_handle(request.workflow_id)
        if request.operation == DispatchOperation.SIGNAL:
            if request.signal_name is None or request.signal is None:
                raise ValueError("Signal dispatch has no decision reference")
            description = await handle.describe(rpc_timeout=self._rpc_timeout)
            if description.status == WorkflowExecutionStatus.RUNNING:
                try:
                    await handle.signal(
                        request.signal_name, request.signal, rpc_timeout=self._rpc_timeout
                    )
                except RPCError as exc:
                    if exc.status != RPCStatusCode.NOT_FOUND:
                        raise
                    # A workflow may complete after describe but before signal.
                    current = await handle.describe(rpc_timeout=self._rpc_timeout)
                    if current.status == WorkflowExecutionStatus.RUNNING:
                        raise
            return
        try:
            description = await handle.describe(rpc_timeout=self._rpc_timeout)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                # Admission rejects later delivery of the cancelled product execution.
                return
            raise
        if description.status != WorkflowExecutionStatus.RUNNING:
            return
        await handle.cancel(rpc_timeout=self._rpc_timeout)
