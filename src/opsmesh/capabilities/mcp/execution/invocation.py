from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.execution.contracts import (
    McpExecutionError,
    McpExecutionPending,
    McpExecutionRequest,
    McpExecutionResult,
    snapshot_audit_metadata,
)
from opsmesh.capabilities.mcp.execution.diagnostics import (
    normalize_failure,
    public_error,
    public_response,
    response_diagnostic,
)
from opsmesh.capabilities.mcp.execution.events import (
    McpExecutionNotifier,
    McpToolCallLogService,
)
from opsmesh.capabilities.mcp.execution.policy import McpExecutionPolicy
from opsmesh.capabilities.mcp.execution.prepared import PreparedMcpExecution
from opsmesh.capabilities.mcp.models import McpCredentialReference, McpServer
from opsmesh.capabilities.mcp.transport.contracts import McpToolAdapter, McpToolAdapterResolver
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.shared.security.redaction import redact_sensitive_text
from opsmesh.shared.telemetry.request_context import log_context
from opsmesh.shared.telemetry.trace_context import current_trace_context, telemetry_span
from opsmesh.shared.utils import canonical_payload, payload_hash


@dataclass(slots=True)
class McpToolInvoker:
    session: Session
    adapter_or_resolver: McpToolAdapter | McpToolAdapterResolver | None

    def prepare(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        server: McpServer,
        snapshot: dict[str, object],
        policy: McpExecutionPolicy,
        credentials: tuple[McpCredentialReference, ...],
    ) -> McpExecutionResult | PreparedMcpExecution:
        log = self._logs().record(
            request=request,
            server_id=server.id,
            status="running",
            response=None,
            error=None,
            snapshot=snapshot,
            run=run,
        )
        self._notify_called(request=request, run=run, server=server, snapshot=snapshot)
        started = monotonic()
        try:
            self._enforce_payload_size(request.arguments, policy.max_input_bytes)
            with log_context(
                workspace_id=request.workspace_id,
                run_id=run.id,
                runtime_id=run.execution_runtime_id or run.runtime_id,
                task_id=run.task_id,
                tool_call_id=request.tool_call_id,
            ):
                operation = self._adapter_for(server).prepare(
                    server=server,
                    tool_name=request.tool_name,
                    arguments=request.arguments,
                    credential_refs=list(credentials),
                    timeout_seconds=policy.timeout_seconds,
                )
        except McpExecutionPending as error:
            return self._record_pending(
                request=request,
                run=run,
                server=server,
                snapshot=snapshot,
                pending=error,
                latency_ms=_latency_ms(started),
                log_id=log.id,
            )
        except McpExecutionError as error:
            return self._record_failure(
                request=request,
                run=run,
                server=server,
                snapshot=snapshot,
                error=_normalized_error(error),
                latency_ms=_latency_ms(started),
                log_id=log.id,
            )
        except Exception as error:
            return self._record_failure(
                request=request,
                run=run,
                server=server,
                snapshot=snapshot,
                error=_normalized_error(error),
                latency_ms=_latency_ms(started),
                log_id=log.id,
            )
        return PreparedMcpExecution(
            request,
            server.id,
            log.id,
            snapshot,
            policy,
            operation,
            started,
            current_trace_context(),
        )

    def complete(
        self,
        prepared: PreparedMcpExecution,
        response: dict[str, object] | None,
        error: BaseException | None,
    ) -> McpExecutionResult:
        with telemetry_span("opsmesh.mcp.tool.complete", parent=prepared.trace):
            return self._complete(prepared, response, error)

    def _complete(
        self,
        prepared: PreparedMcpExecution,
        response: dict[str, object] | None,
        error: BaseException | None,
    ) -> McpExecutionResult:
        request = prepared.request
        run = self.session.get(AgentRun, request.agent_run_id)
        server = self.session.get(McpServer, prepared.server_id)
        if (
            run is None
            or server is None
            or run.workspace_id != request.workspace_id
            or server.workspace_id != request.workspace_id
        ):
            raise ValueError("MCP completion is outside its workspace")
        if error is None:
            if response is None:
                raise ValueError("MCP completion is missing a result")
            try:
                self._enforce_payload_size(response, prepared.policy.max_output_bytes)
            except McpExecutionError as failure:
                error = failure
        if error is not None:
            payload: dict[str, object] = (
                _normalized_error(error)
                if isinstance(error, Exception)
                else {
                    "code": "mcp_execution_cancelled",
                    "message": "Runtime operation cancelled",
                    "category": "cancelled",
                    "phase": "tool",
                }
            )
            return self._record_failure(
                request=request,
                run=run,
                server=server,
                snapshot=prepared.snapshot,
                error=payload,
                latency_ms=_latency_ms(prepared.started),
                log_id=prepared.log_id,
            )
        assert response is not None
        if response.get("isError") is True:
            return self._record_failure(
                request=request,
                run=run,
                server=server,
                snapshot=prepared.snapshot,
                error=normalize_failure(
                    response_diagnostic(response),
                    code="mcp_remote_error",
                    message="MCP tool reported an error",
                ),
                latency_ms=_latency_ms(prepared.started),
                log_id=prepared.log_id,
                response=response,
            )
        return self._record_success(
            request=request,
            run=run,
            server=server,
            snapshot=prepared.snapshot,
            response=response,
            latency_ms=_latency_ms(prepared.started),
            log_id=prepared.log_id,
        )

    def _notify_called(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        server: McpServer,
        snapshot: dict[str, object],
    ) -> None:
        McpExecutionNotifier(self.session).append_run_event(
            run=run,
            event_type="tool.called",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "mcp_server_id": str(server.id),
                "tool_name": request.tool_name,
                "request_sha256": payload_hash(request.arguments),
                **snapshot_audit_metadata(snapshot),
            },
        )

    def _record_pending(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        server: McpServer,
        snapshot: dict[str, object],
        pending: McpExecutionPending,
        latency_ms: int,
        log_id: UUID,
    ) -> McpExecutionResult:
        response = {**pending.response, "status": "waiting_self_hosted"}
        log = self._logs().record(
            request=request,
            server_id=server.id,
            status="waiting_self_hosted",
            response=response,
            error=None,
            snapshot=snapshot,
            run=run,
            latency_ms=latency_ms,
            log_id=log_id,
        )
        notifier = McpExecutionNotifier(self.session)
        notifier.append_run_event(
            run=run,
            event_type="tool.waiting",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "mcp_server_id": str(server.id),
                "tool_name": request.tool_name,
                "pending_code": pending.code,
                "latency_ms": latency_ms,
                **snapshot_audit_metadata(snapshot),
            },
        )
        notifier.append_task_message(
            run=run,
            agent_profile_id=request.agent_profile_id
            if request.agent_profile_id is not None
            else run.agent_profile_id,
            message_type="tool.waiting",
            body=f"MCP tool waiting for self-hosted runtime: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "latency_ms": latency_ms,
                **pending.response,
            },
        )
        notifier.append_execution_audit(
            run=run,
            request=request,
            server_id=server.id,
            log=log,
            action="mcp_tool.waiting_self_hosted",
            snapshot=snapshot,
        )
        self.session.flush()
        return McpExecutionResult(
            status="waiting_self_hosted",
            response=response,
            error=None,
            log_id=log.id,
            latency_ms=latency_ms,
        )

    def _record_failure(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        server: McpServer,
        snapshot: dict[str, object],
        error: dict[str, object],
        latency_ms: int,
        log_id: UUID,
        response: dict[str, object] | None = None,
    ) -> McpExecutionResult:
        error = normalize_failure(
            error,
            code=str(error.get("code", "mcp_execution_failed")),
            message=str(error.get("message", "MCP execution failed")),
            error_id=log_id,
        )
        response = public_response(response) if response is not None else None
        log = self._logs().record(
            request=request,
            server_id=server.id,
            status="failed",
            response=response,
            error={**error, "latency_ms": latency_ms},
            snapshot=snapshot,
            run=run,
            latency_ms=latency_ms,
            log_id=log_id,
        )
        error = public_error(error)
        logging.getLogger(__name__).warning(
            "MCP tool failed",
            extra={
                "workspace_id": str(request.workspace_id),
                "run_id": str(run.id),
                "tool_call_id": request.tool_call_id,
                "tool_name": request.tool_name,
                "error_id": str(log.id),
                "error": error,
                "duration_ms": latency_ms,
            },
        )
        notifier = McpExecutionNotifier(self.session)
        notifier.append_run_event(
            run=run,
            event_type="tool.failed",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "error": error,
                "latency_ms": latency_ms,
                "mcp_tool_call_log_id": str(log.id),
                "tool_call_id": request.tool_call_id,
            },
        )
        notifier.append_task_message(
            run=run,
            agent_profile_id=request.agent_profile_id
            if request.agent_profile_id is not None
            else run.agent_profile_id,
            message_type="tool.failed",
            body=f"MCP tool failed: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "error": error,
                "latency_ms": latency_ms,
            },
        )
        notifier.append_execution_audit(
            run=run,
            request=request,
            server_id=server.id,
            log=log,
            action="mcp_tool.failed",
            snapshot=snapshot,
        )
        self.session.flush()
        return McpExecutionResult(
            status="failed",
            response=response,
            error=error,
            log_id=log.id,
            latency_ms=latency_ms,
        )

    def _record_success(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        server: McpServer,
        snapshot: dict[str, object],
        response: dict[str, object],
        latency_ms: int,
        log_id: UUID,
    ) -> McpExecutionResult:
        log = self._logs().record(
            request=request,
            server_id=server.id,
            status="completed",
            response=response,
            error=None,
            snapshot=snapshot,
            run=run,
            latency_ms=latency_ms,
            log_id=log_id,
        )
        notifier = McpExecutionNotifier(self.session)
        notifier.append_run_event(
            run=run,
            event_type="tool.completed",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "mcp_server_id": str(server.id),
                "tool_name": request.tool_name,
                "response_sha256": payload_hash(response),
                "latency_ms": latency_ms,
                **snapshot_audit_metadata(snapshot),
            },
        )
        notifier.append_task_message(
            run=run,
            agent_profile_id=request.agent_profile_id
            if request.agent_profile_id is not None
            else run.agent_profile_id,
            message_type="tool.completed",
            body=f"MCP tool completed: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "latency_ms": latency_ms,
                "response_sha256": payload_hash(response),
            },
        )
        notifier.append_execution_audit(
            run=run,
            request=request,
            server_id=server.id,
            log=log,
            action="mcp_tool.completed",
            snapshot=snapshot,
        )
        self.session.flush()
        return McpExecutionResult(
            status="completed",
            response=response,
            error=None,
            log_id=log.id,
            latency_ms=latency_ms,
        )

    def _enforce_payload_size(self, payload: dict[str, object], max_bytes: int) -> None:
        size = len(canonical_payload(payload).encode("utf-8"))
        if size > max_bytes:
            raise McpExecutionError(
                "MCP payload exceeds configured size limit",
                code="mcp_payload_too_large",
            )

    def _adapter_for(self, server: McpServer) -> McpToolAdapter:
        if self.adapter_or_resolver is None:
            raise ValueError("MCP preparation requires an adapter resolver")
        if isinstance(self.adapter_or_resolver, McpToolAdapterResolver):
            adapter = self.adapter_or_resolver.resolve(server)
            return adapter
        return self.adapter_or_resolver

    def _logs(self) -> McpToolCallLogService:
        return McpToolCallLogService(self.session)


def _normalized_error(exc: Exception) -> dict[str, object]:
    if isinstance(exc, McpExecutionError):
        return normalize_failure(
            exc.diagnostic, code=exc.code, message=redact_sensitive_text(str(exc))
        )
    return normalize_failure(
        {
            "exception_type": type(exc).__name__,
            "phase": "control",
            "category": "network" if isinstance(exc, TimeoutError) else "execution",
        },
        code="mcp_adapter_failed",
        message="MCP adapter failed",
    )


def _latency_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
