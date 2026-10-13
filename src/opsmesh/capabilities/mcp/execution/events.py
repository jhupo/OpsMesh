from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.capabilities.governance.agent_policy import agent_allowed_mcp_tool_names
from opsmesh.capabilities.mcp.execution.contracts import (
    MCPExecutionRequest,
    MCPToolCallLogRequest,
    snapshot_audit_metadata,
)
from opsmesh.capabilities.mcp.execution.payloads import (
    error_code,
    hash_from_payload,
    response_hash_from_payload,
)
from opsmesh.capabilities.mcp.execution.payloads import response_hash as mcp_response_hash
from opsmesh.capabilities.mcp.models import MCPServer, MCPToolAllowlist, MCPToolCallLog
from opsmesh.capabilities.mcp.policy import require_mcp_server
from opsmesh.governance.audit.service import AuditService
from opsmesh.orchestration.runs.events import RunEventRecorder
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.message_append import TaskMessageAppendService
from opsmesh.orchestration.tasks.models import TaskMessage
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.redaction import redact_sensitive_payload
from opsmesh.shared.telemetry.request_context import current_evidence_context
from opsmesh.shared.telemetry.trace_context import with_current_trace_metadata
from opsmesh.shared.utils import canonical_payload, payload_hash, uuid_or_none


class MCPToolCallLogQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_mcp_tool_call_logs(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        mcp_server_id: UUID | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
        agent_run_id: UUID | None = None,
        tool_call_id: str | None = None,
        error_id: UUID | None = None,
    ) -> tuple[list[MCPToolCallLog], int]:
        if mcp_server_id is not None:
            require_mcp_server(self._session, workspace_id, mcp_server_id)
        statement = select(MCPToolCallLog).where(MCPToolCallLog.workspace_id == workspace_id)
        if mcp_server_id is not None:
            statement = statement.where(MCPToolCallLog.mcp_server_id == mcp_server_id)
        if tool_name is not None:
            statement = statement.where(MCPToolCallLog.tool_name == tool_name)
        if status is not None:
            statement = statement.where(MCPToolCallLog.status == status)
        if trace_id is not None:
            statement = statement.where(MCPToolCallLog.trace_id == trace_id)
        if request_id is not None:
            statement = statement.where(MCPToolCallLog.request_id == request_id)
        if agent_run_id is not None:
            statement = statement.where(MCPToolCallLog.agent_run_id == agent_run_id)
        if tool_call_id is not None:
            statement = statement.where(MCPToolCallLog.tool_call_id == tool_call_id)
        if error_id is not None:
            statement = statement.where(
                MCPToolCallLog.id == error_id, MCPToolCallLog.status == "failed"
            )
        return self._page(statement.order_by(MCPToolCallLog.created_at.desc()), page)

    def get_diagnostic(self, workspace_id: UUID, log_id: UUID) -> MCPToolCallLog:
        log = self._session.scalar(
            select(MCPToolCallLog).where(
                MCPToolCallLog.workspace_id == workspace_id, MCPToolCallLog.id == log_id
            )
        )
        if log is None:
            raise ValueError("MCP call log not found")
        return log

    def get_diagnostic_notification(self, workspace_id: UUID, event_id: UUID) -> RunEvent:
        event = self._session.scalar(
            select(RunEvent).where(
                RunEvent.workspace_id == workspace_id,
                RunEvent.id == event_id,
                RunEvent.event_type == "mcp.diagnostic.notification",
            )
        )
        if event is None:
            raise ValueError("MCP diagnostic notification not found")
        return event

    def _page(
        self,
        statement: Select[tuple[MCPToolCallLog]],
        page: PageParams,
    ) -> tuple[list[MCPToolCallLog], int]:
        return page_scalars(self._session, statement, page)


def _latency_ms_from_payload(
    response: dict[str, object] | None,
    error: dict[str, object] | None,
) -> int | None:
    for payload in (response, error):
        if not isinstance(payload, dict):
            continue
        value = payload.get("latency_ms")
        if isinstance(value, int) and value >= 0:
            return value
    return None


@dataclass(slots=True)
class MCPToolCallRecorder:
    session: Session

    def record_report(
        self,
        workspace_id: UUID,
        data: MCPToolCallLogRequest,
    ) -> MCPToolCallLog:
        if data.mcp_server_id is not None:
            require_mcp_server(self.session, workspace_id, data.mcp_server_id)
        allow = self._allowed_tool_by_name(workspace_id, data.tool_name)
        if allow is None:
            raise ValueError("MCP tool is not allowed for this workspace")
        if data.mcp_server_id is not None and allow.mcp_server_id != data.mcp_server_id:
            raise ValueError("MCP tool is not allowed for this server")
        run = self._mcp_log_run_context(workspace_id, data)
        agent = (
            self.session.get(AgentProfile, run.agent_profile_id)
            if run is not None and run.agent_profile_id is not None
            else None
        )
        agent_allowed_tools = agent_allowed_mcp_tool_names(agent) if agent is not None else None
        if agent_allowed_tools is not None and data.tool_name not in agent_allowed_tools:
            raise ValueError("MCP tool is not allowed for this agent")
        request_payload = redact_sensitive_payload(data.request)
        response_payload = (
            redact_sensitive_payload(data.response) if data.response is not None else None
        )
        error_payload = redact_sensitive_payload(data.error) if data.error is not None else None
        payload = data.model_dump(exclude={"request", "response", "error"})
        payload.update(
            {
                "request": with_current_trace_metadata(request_payload),
                "response": response_payload,
                "error": error_payload,
            }
        )
        if run is not None:
            payload.update(
                {
                    "task_id": run.task_id,
                    "task_step_id": run.task_step_id,
                    "agent_profile_id": run.agent_profile_id,
                }
            )
        evidence = current_evidence_context()
        log = MCPToolCallLog(
            workspace_id=workspace_id,
            latency_ms=_latency_ms_from_payload(response_payload, error_payload),
            argument_sha256=hash_from_payload(request_payload, "arguments_sha256"),
            response_sha256=response_hash_from_payload(data.response),
            error_code=error_code(error_payload),
            request_id=evidence.get("request_id"),
            worker_id=evidence.get("worker_id"),
            runtime_id=(
                str(run.runtime_id)
                if run is not None and run.runtime_id is not None
                else evidence.get("runtime_id")
            ),
            trace_id=evidence.get("trace_id"),
            span_id=evidence.get("span_id"),
            created_at=datetime.now(UTC),
            **payload,
        )
        self.session.add(log)
        self.session.flush()
        return log

    def _mcp_log_run_context(
        self,
        workspace_id: UUID,
        data: MCPToolCallLogRequest,
    ) -> AgentRun | None:
        if data.agent_run_id is None:
            return None
        run = self.session.get(AgentRun, data.agent_run_id)
        if run is None or run.workspace_id != workspace_id:
            raise ValueError("Agent run not found")
        return run

    def _allowed_tool_by_name(self, workspace_id: UUID, tool_name: str) -> MCPToolAllowlist | None:
        return self.session.scalar(
            select(MCPToolAllowlist)
            .join(MCPServer, MCPServer.id == MCPToolAllowlist.mcp_server_id)
            .where(
                MCPToolAllowlist.workspace_id == workspace_id,
                MCPToolAllowlist.tool_name == tool_name,
                MCPToolAllowlist.status == "active",
                MCPToolAllowlist.platform_blocked.is_(False),
                MCPServer.status == "active",
                MCPServer.platform_blocked.is_(False),
            )
        )

    def record(
        self,
        *,
        request: MCPExecutionRequest,
        server_id: UUID | None,
        status: str,
        response: dict[str, object] | None,
        error: dict[str, object] | None,
        snapshot: dict[str, object] | None = None,
        run: AgentRun | None = None,
        latency_ms: int | None = None,
        log_id: UUID | None = None,
    ) -> MCPToolCallLog:
        argument_sha256 = payload_hash(request.arguments)
        response_sha256 = mcp_response_hash(response) if status in {"completed", "failed"} else None
        redacted_response = redact_sensitive_payload(response) if response is not None else None
        redacted_error = redact_sensitive_payload(error) if error is not None else None
        evidence = current_evidence_context()
        values = dict(
            workspace_id=request.workspace_id,
            mcp_server_id=server_id,
            agent_run_id=request.agent_run_id,
            task_id=run.task_id if run is not None else None,
            task_step_id=run.task_step_id if run is not None else None,
            agent_profile_id=uuid_or_none((snapshot or {}).get("agent_profile_id")),
            tool_name=request.tool_name,
            tool_call_id=request.tool_call_id,
            status=status,
            latency_ms=latency_ms,
            argument_sha256=argument_sha256,
            response_sha256=response_sha256,
            error_code=error_code(error),
            request_id=evidence.get("request_id"),
            worker_id=evidence.get("worker_id"),
            runtime_id=(
                str(run.runtime_id)
                if run is not None and run.runtime_id is not None
                else evidence.get("runtime_id")
            ),
            trace_id=evidence.get("trace_id"),
            span_id=evidence.get("span_id"),
            request=with_current_trace_metadata(
                {
                    "arguments_sha256": argument_sha256,
                    "argument_bytes": len(canonical_payload(request.arguments).encode("utf-8")),
                    "cost_correlation_agent_run_id": str(request.agent_run_id),
                    "tool_call_id": request.tool_call_id,
                    **snapshot_audit_metadata(snapshot or {}),
                }
            ),
            response=redacted_response,
            error=redacted_error,
            created_at=datetime.now(UTC),
        )
        if log_id is None:
            log = MCPToolCallLog(**values)
            self.session.add(log)
        else:
            existing_log = self.session.scalar(
                select(MCPToolCallLog)
                .where(
                    MCPToolCallLog.id == log_id,
                    MCPToolCallLog.workspace_id == request.workspace_id,
                    MCPToolCallLog.agent_run_id == request.agent_run_id,
                    MCPToolCallLog.mcp_server_id == server_id,
                    MCPToolCallLog.tool_name == request.tool_name,
                )
                .with_for_update()
            )
            if existing_log is None or existing_log.status != "running":
                raise ValueError("MCP completion has no active admission record")
            log = existing_log
            for key in (
                "status",
                "latency_ms",
                "response_sha256",
                "error_code",
                "response",
                "error",
            ):
                setattr(log, key, values[key])
        self.session.flush()
        return log


@dataclass(slots=True)
class MCPExecutionNotifier:
    session: Session

    def append_run_event(
        self,
        *,
        run: AgentRun,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> RunEvent:
        return RunEventRecorder(self.session).append_event(
            run,
            event_type,
            message,
            metadata,
        )

    def append_task_message(
        self,
        *,
        run: AgentRun,
        agent_profile_id: UUID | None,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> TaskMessage | None:
        if run.task_id is None:
            return None
        return TaskMessageAppendService(self.session).append(
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            task_step_id=run.task_step_id,
            agent_run_id=run.id,
            agent_profile_id=agent_profile_id,
            message_type=message_type,
            body=body,
            payload=payload,
        )

    def append_execution_audit(
        self,
        *,
        run: AgentRun,
        request: MCPExecutionRequest,
        server_id: UUID | None,
        log: MCPToolCallLog,
        action: str,
        snapshot: dict[str, object],
    ) -> None:
        AuditService(self.session).record_system_action(
            workspace_id=run.workspace_id,
            action=action,
            target_type="agent_run",
            target_id=run.id,
            metadata={
                "mcp_tool_call_log_id": str(log.id),
                "mcp_server_id": str(server_id) if server_id is not None else None,
                "tool_name": request.tool_name,
                "status": log.status,
                "latency_ms": log.latency_ms,
                "argument_sha256": log.argument_sha256,
                "response_sha256": log.response_sha256,
                "error_code": log.error_code,
                "approval_id": str(log.approval_id) if log.approval_id is not None else None,
                "cost_correlation_agent_run_id": str(run.id),
                **snapshot_audit_metadata(snapshot),
            },
        )
