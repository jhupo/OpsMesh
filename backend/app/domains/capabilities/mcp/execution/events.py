from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.capabilities.governance.agent_policy import agent_allowed_mcp_tool_names
from backend.app.domains.capabilities.mcp.execution.contracts import (
    McpExecutionRequest,
    McpToolCallLogRequest,
    snapshot_audit_metadata,
)
from backend.app.domains.capabilities.mcp.execution.payloads import (
    canonical_payload,
    error_code,
    hash_from_payload,
    payload_hash,
    response_hash_from_payload,
)
from backend.app.domains.capabilities.mcp.execution.payloads import (
    response_hash as mcp_response_hash,
)
from backend.app.domains.capabilities.mcp.models import (
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
)
from backend.app.domains.capabilities.mcp.policy import require_mcp_server
from backend.app.domains.orchestration.runs.events import RunEventRecorder
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.domains.orchestration.tasks.message_append import TaskMessageAppendService
from backend.app.domains.orchestration.tasks.models import TaskMessage


class McpToolCallLogQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def log_mcp_tool_call(
        self,
        workspace_id: UUID,
        data: McpToolCallLogRequest,
    ) -> McpToolCallLog:
        if data.mcp_server_id is not None:
            require_mcp_server(self._session, workspace_id, data.mcp_server_id)
        allow = self._allowed_tool_by_name(workspace_id, data.tool_name)
        if allow is None:
            raise ValueError("MCP tool is not allowed for this workspace")
        if data.mcp_server_id is not None and allow.mcp_server_id != data.mcp_server_id:
            raise ValueError("MCP tool is not allowed for this server")
        run = self._mcp_log_run_context(workspace_id, data)
        agent = (
            self._session.get(AgentProfile, run.agent_profile_id)
            if run is not None and run.agent_profile_id is not None
            else None
        )
        agent_allowed_tools = (
            agent_allowed_mcp_tool_names(agent) if agent is not None else None
        )
        if agent_allowed_tools is not None and data.tool_name not in agent_allowed_tools:
            raise ValueError("MCP tool is not allowed for this agent")
        request_payload = data.request
        response_payload = data.response
        error_payload = data.error
        payload = data.model_dump()
        if run is not None:
            payload.update(
                {
                    "task_id": run.task_id,
                    "task_step_id": run.task_step_id,
                    "agent_profile_id": run.agent_profile_id,
                }
            )
        log = McpToolCallLog(
            workspace_id=workspace_id,
            latency_ms=_latency_ms_from_payload(response_payload, error_payload),
            argument_sha256=hash_from_payload(request_payload, "arguments_sha256"),
            response_sha256=response_hash_from_payload(response_payload),
            error_code=error_code(error_payload),
            created_at=datetime.now(UTC),
            **payload,
        )
        self._session.add(log)
        self._session.commit()
        self._session.refresh(log)
        return log

    def list_mcp_tool_call_logs(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        mcp_server_id: UUID | None = None,
        tool_name: str | None = None,
        status: str | None = None,
    ) -> tuple[list[McpToolCallLog], int]:
        if mcp_server_id is not None:
            require_mcp_server(self._session, workspace_id, mcp_server_id)
        statement = select(McpToolCallLog).where(McpToolCallLog.workspace_id == workspace_id)
        if mcp_server_id is not None:
            statement = statement.where(McpToolCallLog.mcp_server_id == mcp_server_id)
        if tool_name is not None:
            statement = statement.where(McpToolCallLog.tool_name == tool_name)
        if status is not None:
            statement = statement.where(McpToolCallLog.status == status)
        return self._page(statement.order_by(McpToolCallLog.created_at.desc()), page)

    def _mcp_log_run_context(
        self,
        workspace_id: UUID,
        data: McpToolCallLogRequest,
    ) -> AgentRun | None:
        if data.agent_run_id is None:
            return None
        run = self._session.get(AgentRun, data.agent_run_id)
        if run is None or run.workspace_id != workspace_id:
            raise ValueError("Agent run not found")
        return run

    def _allowed_tool_by_name(self, workspace_id: UUID, tool_name: str) -> McpToolAllowlist | None:
        return self._session.scalar(
            select(McpToolAllowlist)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.tool_name == tool_name,
                McpToolAllowlist.status == "active",
                McpServer.status == "active",
            )
        )

    def _page(
        self,
        statement: Select[tuple[McpToolCallLog]],
        page: PageParams,
    ) -> tuple[list[McpToolCallLog], int]:
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
class McpToolCallLogService:
    session: Session

    def record(
        self,
        *,
        request: McpExecutionRequest,
        server_id: UUID | None,
        status: str,
        response: dict[str, object] | None,
        error: dict[str, object] | None,
        snapshot: dict[str, object] | None = None,
        run: AgentRun | None = None,
        latency_ms: int | None = None,
    ) -> McpToolCallLog:
        argument_sha256 = payload_hash(request.arguments)
        response_sha256 = mcp_response_hash(response)
        log = McpToolCallLog(
            workspace_id=request.workspace_id,
            mcp_server_id=server_id,
            agent_run_id=request.agent_run_id,
            task_id=run.task_id if run is not None else None,
            task_step_id=run.task_step_id if run is not None else None,
            agent_profile_id=run.agent_profile_id if run is not None else None,
            tool_name=request.tool_name,
            status=status,
            latency_ms=latency_ms,
            argument_sha256=argument_sha256,
            response_sha256=response_sha256,
            error_code=error_code(error),
            request={
                "arguments_sha256": argument_sha256,
                "argument_bytes": len(canonical_payload(request.arguments).encode("utf-8")),
                **snapshot_audit_metadata(snapshot or {}),
            },
            response=response,
            error=error,
            created_at=datetime.now(UTC),
        )
        self.session.add(log)
        self.session.flush()
        return log


@dataclass(slots=True)
class McpExecutionNotifier:
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
            agent_profile_id=run.agent_profile_id,
            message_type=message_type,
            body=body,
            payload=payload,
        )
