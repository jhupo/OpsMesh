from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.capabilities.mcp_execution_blocking import McpExecutionBlocker
from backend.app.capabilities.mcp_execution_context import authorization_snapshot
from backend.app.capabilities.mcp_execution_types import McpExecutionRequest
from backend.app.capabilities.mcp_policy import mcp_health_check_stale
from backend.app.capabilities.models import McpServer, McpToolAllowlist
from backend.app.core.config import Settings
from backend.app.runs.models import AgentRun
from backend.app.tools.errors import ToolResourceNotFoundError


@dataclass(frozen=True, slots=True)
class ValidatedMcpExecution:
    run: AgentRun
    snapshot: dict[str, object]
    allow: McpToolAllowlist
    server: McpServer


@dataclass(slots=True)
class McpExecutionValidator:
    session: Session
    settings: Settings

    def validate(self, request: McpExecutionRequest) -> ValidatedMcpExecution:
        run = self._require_run(request.workspace_id, request.agent_run_id)
        snapshot = authorization_snapshot(run)
        self._validate_snapshot_scope(snapshot, request)
        self._require_runtime_context_tool(request)
        allow, server = self._resolve_allowed_tool(request)
        self._require_snapshot_tool(snapshot, request)
        self._require_server_health(request, server)
        return ValidatedMcpExecution(
            run=run,
            snapshot=snapshot,
            allow=allow,
            server=server,
        )

    def _require_run(self, workspace_id: UUID, run_id: UUID) -> AgentRun:
        run = self.session.get(AgentRun, run_id)
        if run is None or run.workspace_id != workspace_id:
            raise ToolResourceNotFoundError("Agent run not found")
        return run

    def _validate_snapshot_scope(
        self,
        snapshot: dict[str, object],
        request: McpExecutionRequest,
    ) -> None:
        if snapshot.get("workspace_id") not in (None, str(request.workspace_id)):
            self._block(request, "authorization_snapshot_workspace_mismatch")
        if snapshot.get("agent_run_id") not in (None, str(request.agent_run_id)):
            self._block(request, "authorization_snapshot_run_mismatch")

    def _require_runtime_context_tool(self, request: McpExecutionRequest) -> None:
        if request.runtime_allowed_tools is None:
            return
        if request.tool_name in request.runtime_allowed_tools:
            return
        self._block(request, "mcp_tool_not_in_runtime_context")

    def _resolve_allowed_tool(
        self,
        request: McpExecutionRequest,
    ) -> tuple[McpToolAllowlist, McpServer]:
        statement = (
            select(McpToolAllowlist, McpServer)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == request.workspace_id,
                McpToolAllowlist.tool_name == request.tool_name,
                McpToolAllowlist.status == "active",
                McpServer.workspace_id == request.workspace_id,
                McpServer.status == "active",
            )
        )
        if request.mcp_server_id is not None:
            statement = statement.where(McpServer.id == request.mcp_server_id)
        row = self.session.execute(statement).first()
        if row is None:
            self._block(request, "mcp_tool_not_allowed")
            raise AssertionError("blocked MCP execution did not raise")
        return row[0], row[1]

    def _require_snapshot_tool(
        self,
        snapshot: dict[str, object],
        request: McpExecutionRequest,
    ) -> None:
        raw_tools = snapshot.get("allowed_tools")
        if isinstance(raw_tools, list) and all(isinstance(tool, str) for tool in raw_tools):
            if request.tool_name in raw_tools:
                return
            self._block(request, "mcp_tool_not_in_run_snapshot")
        self._block(request, "mcp_tool_snapshot_missing")

    def _require_server_health(self, request: McpExecutionRequest, server: McpServer) -> None:
        if server.health_status == "unhealthy":
            self._block(request, "mcp_server_unhealthy", mcp_server_id=server.id)
        stale_after = timedelta(seconds=self.settings.mcp_health_check_stale_after_seconds)
        if mcp_health_check_stale(server, stale_after=stale_after):
            self._block(request, "mcp_server_health_check_stale", mcp_server_id=server.id)

    def _block(
        self,
        request: McpExecutionRequest,
        reason: str,
        *,
        mcp_server_id: UUID | None = None,
    ) -> None:
        McpExecutionBlocker(self.session).block(
            request,
            reason,
            mcp_server_id=mcp_server_id,
        )
