from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.capabilities.mcp.adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.capabilities.mcp.execution_logs import McpToolCallLogService
from backend.app.capabilities.mcp.execution_notifications import McpExecutionNotifier
from backend.app.capabilities.mcp.execution_policy import McpExecutionPolicy
from backend.app.capabilities.mcp.types import (
    McpExecutionError,
    McpExecutionPending,
    McpExecutionRequest,
    McpExecutionResult,
)
from backend.app.capabilities.mcp_execution_context import snapshot_audit_metadata
from backend.app.capabilities.mcp_payloads import canonical_payload, payload_hash
from backend.app.capabilities.models import McpCredentialReference, McpServer
from backend.app.runs.models import AgentRun
from backend.app.tools.errors import ToolPermissionError


@dataclass(slots=True)
class McpToolInvoker:
    session: Session
    adapter_or_resolver: McpToolAdapter | McpToolAdapterResolver

    async def invoke(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        server: McpServer,
        snapshot: dict[str, object],
        policy: McpExecutionPolicy,
    ) -> McpExecutionResult:
        self._notify_called(request=request, run=run, server=server, snapshot=snapshot)
        started = monotonic()
        try:
            self._enforce_payload_size(request.arguments, policy.max_input_bytes)
            response = await self._adapter_for(server).call(
                server=server,
                tool_name=request.tool_name,
                arguments=request.arguments,
                credential_refs=self._credential_refs(request.workspace_id, server.id),
                timeout_seconds=policy.timeout_seconds,
            )
            self._enforce_payload_size(response, policy.max_output_bytes)
        except McpExecutionPending as exc:
            return self._record_pending(
                request=request,
                run=run,
                server=server,
                snapshot=snapshot,
                pending=exc,
                latency_ms=_latency_ms(started),
            )
        except ToolPermissionError:
            raise
        except McpExecutionError as exc:
            return self._record_failure(
                request=request,
                run=run,
                server=server,
                snapshot=snapshot,
                error=_normalized_error(exc),
                latency_ms=_latency_ms(started),
            )
        except Exception as exc:
            self._record_crash(
                request=request,
                run=run,
                server=server,
                snapshot=snapshot,
                error={"code": "mcp_adapter_crashed", "message": exc.__class__.__name__},
                latency_ms=_latency_ms(started),
            )
            raise

        return self._record_success(
            request=request,
            run=run,
            server=server,
            snapshot=snapshot,
            response=response,
            latency_ms=_latency_ms(started),
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
    ) -> McpExecutionResult:
        response = {**pending.response, "status": "waiting_self_hosted"}
        log = self._logs().record(
            request=request,
            server_id=server.id,
            status="waiting_self_hosted",
            response={"result": response, "latency_ms": latency_ms},
            error=None,
            snapshot=snapshot,
            run=run,
            latency_ms=latency_ms,
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
            message_type="tool.waiting",
            body=f"MCP tool waiting for self-hosted runtime: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "latency_ms": latency_ms,
                **pending.response,
            },
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
    ) -> McpExecutionResult:
        log = self._logs().record(
            request=request,
            server_id=server.id,
            status="failed",
            response=None,
            error={**error, "latency_ms": latency_ms},
            snapshot=snapshot,
            run=run,
            latency_ms=latency_ms,
        )
        notifier = McpExecutionNotifier(self.session)
        notifier.append_run_event(
            run=run,
            event_type="tool.failed",
            message=request.tool_name,
            metadata={"tool_kind": "mcp", "error": error, "latency_ms": latency_ms},
        )
        notifier.append_task_message(
            run=run,
            message_type="tool.failed",
            body=f"MCP tool failed: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "error": error,
                "latency_ms": latency_ms,
            },
        )
        self.session.flush()
        return McpExecutionResult(
            status="failed",
            response=None,
            error=error,
            log_id=log.id,
            latency_ms=latency_ms,
        )

    def _record_crash(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        server: McpServer,
        snapshot: dict[str, object],
        error: dict[str, object],
        latency_ms: int,
    ) -> None:
        self._logs().record(
            request=request,
            server_id=server.id,
            status="failed",
            response=None,
            error={**error, "latency_ms": latency_ms},
            snapshot=snapshot,
            run=run,
            latency_ms=latency_ms,
        )
        McpExecutionNotifier(self.session).append_run_event(
            run=run,
            event_type="tool.failed",
            message=request.tool_name,
            metadata={"tool_kind": "mcp", "error": error, "latency_ms": latency_ms},
        )
        self.session.flush()

    def _record_success(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        server: McpServer,
        snapshot: dict[str, object],
        response: dict[str, object],
        latency_ms: int,
    ) -> McpExecutionResult:
        log = self._logs().record(
            request=request,
            server_id=server.id,
            status="completed",
            response={"result": response, "latency_ms": latency_ms},
            error=None,
            snapshot=snapshot,
            run=run,
            latency_ms=latency_ms,
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
            message_type="tool.completed",
            body=f"MCP tool completed: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "latency_ms": latency_ms,
                "response_sha256": payload_hash(response),
            },
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

    def _credential_refs(
        self,
        workspace_id: UUID,
        server_id: UUID,
    ) -> list[McpCredentialReference]:
        return list(
            self.session.scalars(
                select(McpCredentialReference)
                .where(
                    McpCredentialReference.workspace_id == workspace_id,
                    McpCredentialReference.status == "active",
                    or_(
                        McpCredentialReference.mcp_server_id == server_id,
                        McpCredentialReference.mcp_server_id.is_(None),
                    ),
                )
                .order_by(McpCredentialReference.created_at.asc())
            )
        )

    def _adapter_for(self, server: McpServer) -> McpToolAdapter:
        if isinstance(self.adapter_or_resolver, McpToolAdapterResolver):
            adapter = self.adapter_or_resolver.resolve(server)
            if not hasattr(adapter, "call"):
                raise McpExecutionError(
                    "MCP adapter resolver returned an invalid adapter",
                    code="mcp_adapter_invalid",
                )
            return adapter
        return self.adapter_or_resolver

    def _logs(self) -> McpToolCallLogService:
        return McpToolCallLogService(self.session)


def _normalized_error(exc: Exception) -> dict[str, object]:
    if isinstance(exc, McpExecutionError):
        return {"code": exc.code, "message": str(exc)}
    return {"code": "mcp_adapter_failed", "message": exc.__class__.__name__}


def _latency_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
