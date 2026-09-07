from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.capabilities.mcp_execution_context import (
    authorization_snapshot,
    snapshot_audit_metadata,
)
from backend.app.capabilities.mcp_execution_logs import McpToolCallLogService
from backend.app.capabilities.mcp_execution_notifications import McpExecutionNotifier
from backend.app.capabilities.mcp_execution_types import McpExecutionRequest
from backend.app.core.trace_context import with_current_trace_metadata
from backend.app.runs.models import AgentRun
from backend.app.security.models import SecurityEvent
from backend.app.tools.errors import ToolPermissionError


@dataclass(slots=True)
class McpExecutionBlocker:
    session: Session

    def block(
        self,
        request: McpExecutionRequest,
        reason: str,
        *,
        mcp_server_id: UUID | None = None,
    ) -> NoReturn:
        run = self.session.get(AgentRun, request.agent_run_id)
        resolved_server_id = mcp_server_id or request.mcp_server_id
        snapshot = (
            authorization_snapshot(run)
            if run is not None and run.workspace_id == request.workspace_id
            else {}
        )
        error = {"code": reason, "message": "MCP tool invocation was blocked by policy"}
        scoped_run = run if run is not None and run.workspace_id == request.workspace_id else None
        if scoped_run is not None:
            self._notify_blocked(
                request=request,
                run=scoped_run,
                reason=reason,
                resolved_server_id=resolved_server_id,
                snapshot=snapshot,
            )
        McpToolCallLogService(self.session).record(
            request=request,
            server_id=resolved_server_id,
            status="blocked",
            response=None,
            error=error,
            snapshot=snapshot,
            run=scoped_run,
            latency_ms=0,
        )
        self._record_security_event(
            request=request,
            reason=reason,
            resolved_server_id=resolved_server_id,
            snapshot=snapshot,
        )
        self.session.flush()
        raise ToolPermissionError(f"MCP tool blocked: {reason}")

    def _notify_blocked(
        self,
        *,
        request: McpExecutionRequest,
        run: AgentRun,
        reason: str,
        resolved_server_id: UUID | None,
        snapshot: dict[str, object],
    ) -> None:
        notifier = McpExecutionNotifier(self.session)
        notifier.append_run_event(
            run=run,
            event_type="tool.blocked",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "mcp_server_id": str(resolved_server_id)
                if resolved_server_id is not None
                else None,
                "tool_name": request.tool_name,
                "reason": reason,
                **snapshot_audit_metadata(snapshot),
            },
        )
        notifier.append_task_message(
            run=run,
            message_type="tool.blocked",
            body=f"MCP tool blocked: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(resolved_server_id)
                if resolved_server_id is not None
                else None,
                "reason": reason,
            },
        )

    def _record_security_event(
        self,
        *,
        request: McpExecutionRequest,
        reason: str,
        resolved_server_id: UUID | None,
        snapshot: dict[str, object],
    ) -> None:
        self.session.add(
            SecurityEvent(
                workspace_id=request.workspace_id,
                user_id=None,
                action="mcp_tool.blocked",
                outcome="blocked",
                severity="high",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="internal:mcp_tool_execution",
                method="WORKER",
                reason=reason,
                event_metadata=with_current_trace_metadata(
                    {
                        "agent_run_id": str(request.agent_run_id),
                        "mcp_server_id": str(resolved_server_id)
                        if resolved_server_id is not None
                        else None,
                        "tool_name": request.tool_name,
                        **snapshot_audit_metadata(snapshot),
                    }
                ),
                created_at=datetime.now(UTC),
            )
        )
