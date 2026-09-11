from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.capabilities.effective_catalog import effective_catalog_fingerprint
from backend.app.capabilities.mcp.types import McpExecutionRequest
from backend.app.capabilities.mcp_execution_blocking import McpExecutionBlocker
from backend.app.capabilities.mcp_execution_context import authorization_snapshot
from backend.app.capabilities.mcp_policy import mcp_health_check_stale
from backend.app.capabilities.models import McpServer, McpToolAllowlist
from backend.app.capabilities.schema_validation import validate_parameters
from backend.app.core.config import Settings
from backend.app.orchestration.run_authorization_integrity import (
    authorization_snapshot_fingerprint,
)
from backend.app.runs.models import AgentRun
from backend.app.tools.errors import ToolResourceNotFoundError


@dataclass(frozen=True, slots=True)
class ValidatedMcpExecution:
    request: McpExecutionRequest
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
        descriptor, parameters, locked = self._require_snapshot_tool(snapshot, request)
        try:
            snapshot_server_id = _required_uuid(descriptor.get("mcp_server_id"))
        except ValueError:
            self._block(request, "mcp_tool_snapshot_invalid")
        prepared_request = replace(
            request,
            mcp_server_id=snapshot_server_id,
            arguments=self._validated_arguments(
                request,
                descriptor=descriptor,
                parameters=parameters,
                locked=locked,
            ),
        )
        allow, server = self._resolve_allowed_tool(prepared_request, descriptor)
        self._require_server_health(prepared_request, server)
        return ValidatedMcpExecution(
            request=prepared_request,
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
        if snapshot.get("version") != 2:
            self._block(request, "authorization_snapshot_version_unsupported")
        fingerprint = snapshot.get("fingerprint")
        if (
            not isinstance(fingerprint, str)
            or fingerprint != authorization_snapshot_fingerprint(snapshot)
        ):
            self._block(request, "authorization_snapshot_fingerprint_mismatch")
        catalog = snapshot.get("capability_catalog")
        catalog_fingerprint = catalog.get("fingerprint") if isinstance(catalog, dict) else None
        if (
            not isinstance(catalog, dict)
            or not isinstance(catalog_fingerprint, str)
            or catalog_fingerprint != effective_catalog_fingerprint(catalog)
        ):
            self._block(request, "capability_catalog_fingerprint_mismatch")
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
        descriptor: dict[str, object],
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
        rows = self.session.execute(statement).all()
        if len(rows) != 1:
            self._block(request, "mcp_tool_not_allowed")
        row = rows[0]
        try:
            snapshot_allowlist_id = _required_uuid(descriptor.get("mcp_tool_allowlist_id"))
        except ValueError:
            self._block(request, "mcp_tool_snapshot_invalid")
        if row[0].id != snapshot_allowlist_id:
            self._block(
                request,
                "mcp_tool_allowlist_provenance_mismatch",
                mcp_server_id=row[1].id,
            )
        return row[0], row[1]

    def _require_snapshot_tool(
        self,
        snapshot: dict[str, object],
        request: McpExecutionRequest,
    ) -> tuple[dict[str, object], dict[str, object], tuple[str, ...]]:
        catalog = snapshot.get("capability_catalog")
        tools = catalog.get("tools") if isinstance(catalog, dict) else None
        if not isinstance(tools, list):
            self._block(request, "mcp_tool_snapshot_missing")
        matches: list[dict[str, object]] = []
        for item in tools:
            if not isinstance(item, dict):
                continue
            descriptor = item.get("descriptor")
            if not isinstance(descriptor, dict):
                continue
            if descriptor.get("name") != request.tool_name or descriptor.get("source") != "mcp":
                continue
            if request.mcp_server_id is not None and descriptor.get("mcp_server_id") != str(
                request.mcp_server_id
            ):
                continue
            matches.append(item)
        if len(matches) != 1:
            reason = (
                "mcp_tool_not_in_run_snapshot"
                if not matches
                else "mcp_tool_snapshot_ambiguous"
            )
            self._block(request, reason)
        item = matches[0]
        descriptor = item.get("descriptor")
        parameters = item.get("parameters")
        locked = item.get("locked_parameters")
        if (
            not isinstance(descriptor, dict)
            or not isinstance(parameters, dict)
            or not isinstance(locked, list)
            or not all(isinstance(field, str) for field in locked)
        ):
            self._block(request, "mcp_tool_snapshot_invalid")
        return descriptor, parameters, tuple(locked)

    def _validated_arguments(
        self,
        request: McpExecutionRequest,
        *,
        descriptor: dict[str, object],
        parameters: dict[str, object],
        locked: tuple[str, ...],
    ) -> dict[str, object]:
        arguments = dict(parameters)
        for field, value in request.arguments.items():
            if field in locked and arguments.get(field) != value:
                self._block(request, "mcp_tool_parameter_locked")
            arguments[field] = value
        schema = descriptor.get("input_schema")
        if not isinstance(schema, dict):
            self._block(request, "mcp_tool_snapshot_invalid")
        try:
            validate_parameters(arguments, schema, label="MCP tool arguments")
        except ValueError:
            self._block(request, "mcp_tool_arguments_invalid")
        return arguments

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
    ) -> NoReturn:
        McpExecutionBlocker(self.session).block(
            request,
            reason,
            mcp_server_id=mcp_server_id,
        )
        raise AssertionError("blocked MCP execution did not raise")


def _required_uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("MCP tool snapshot UUID is invalid") from exc
