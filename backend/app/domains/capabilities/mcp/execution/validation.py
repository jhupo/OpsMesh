from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import NoReturn
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.domains.capabilities.catalog.contracts import CapabilityToolDescriptor
from backend.app.domains.capabilities.catalog.effective import effective_catalog_fingerprint
from backend.app.domains.capabilities.mcp.catalog.rules import (
    requires_credentials as mcp_server_requires_credentials,
)
from backend.app.domains.capabilities.mcp.execution.blocking import McpExecutionBlocker
from backend.app.domains.capabilities.mcp.execution.contracts import McpExecutionRequest
from backend.app.domains.capabilities.mcp.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
)
from backend.app.domains.capabilities.mcp.policy import mcp_health_check_stale
from backend.app.domains.capabilities.resources.schema import validate_parameters
from backend.app.domains.capabilities.tools.contracts import ToolResourceNotFoundError
from backend.app.domains.orchestration.runs.models import (
    AUTHORIZATION_SNAPSHOT_VERSION,
    AgentRun,
    authorization_snapshot_fingerprint,
)
from backend.app.domains.orchestration.runs.queries import authorization_snapshot_for_run


@dataclass(frozen=True, slots=True)
class ValidatedMcpExecution:
    request: McpExecutionRequest
    run: AgentRun
    snapshot: dict[str, object]
    descriptor: CapabilityToolDescriptor
    server: McpServer
    credentials: tuple[McpCredentialReference, ...]


@dataclass(slots=True)
class McpExecutionValidator:
    session: Session
    settings: Settings

    def validate(self, request: McpExecutionRequest) -> ValidatedMcpExecution:
        run = self._require_run(request.workspace_id, request.agent_run_id)
        snapshot = authorization_snapshot_for_run(run)
        self._validate_snapshot_scope(snapshot, request)
        self._require_runtime_context_tool(request)
        descriptor, parameters, locked = self._require_snapshot_tool(snapshot, request)
        snapshot_server_id = descriptor.mcp_server_id
        if snapshot_server_id is None:
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
        _, server = self._resolve_allowed_tool(prepared_request, descriptor)
        self._require_server_health(prepared_request, server)
        credentials = self._resolve_credentials(prepared_request, descriptor, server)
        return ValidatedMcpExecution(
            request=prepared_request,
            run=run,
            snapshot=snapshot,
            descriptor=descriptor,
            server=server,
            credentials=credentials,
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
        if snapshot.get("version") != AUTHORIZATION_SNAPSHOT_VERSION:
            self._block(request, "authorization_snapshot_version_unsupported")
        fingerprint = snapshot.get("fingerprint")
        if not isinstance(fingerprint, str) or fingerprint != authorization_snapshot_fingerprint(
            snapshot
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
        descriptor: CapabilityToolDescriptor,
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
        snapshot_allowlist_id = descriptor.mcp_tool_allowlist_id
        if snapshot_allowlist_id is None:
            self._block(request, "mcp_tool_snapshot_invalid")
        if row[0].id != snapshot_allowlist_id:
            self._block(
                request,
                "mcp_tool_allowlist_provenance_mismatch",
                mcp_server_id=row[1].id,
            )
        server_version = descriptor.mcp_server_configuration_version
        tool_version = descriptor.mcp_tool_configuration_version
        if server_version is None or tool_version is None:
            self._block(request, "mcp_tool_snapshot_invalid", mcp_server_id=row[1].id)
        if row[1].configuration_version != server_version:
            self._block(request, "mcp_server_configuration_stale", mcp_server_id=row[1].id)
        if row[0].configuration_version != tool_version:
            self._block(request, "mcp_tool_configuration_stale", mcp_server_id=row[1].id)
        if descriptor.mcp_server_type != row[1].server_type:
            self._block(request, "mcp_server_configuration_stale", mcp_server_id=row[1].id)
        return row[0], row[1]

    def _require_snapshot_tool(
        self,
        snapshot: dict[str, object],
        request: McpExecutionRequest,
    ) -> tuple[CapabilityToolDescriptor, dict[str, object], tuple[str, ...]]:
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
                "mcp_tool_not_in_run_snapshot" if not matches else "mcp_tool_snapshot_ambiguous"
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
        try:
            parsed_descriptor = CapabilityToolDescriptor.model_validate(descriptor)
        except ValidationError:
            self._block(request, "mcp_tool_snapshot_invalid")
        if (
            parsed_descriptor.source != "mcp"
            or parsed_descriptor.mcp_server_id is None
            or parsed_descriptor.mcp_tool_allowlist_id is None
            or parsed_descriptor.mcp_server_type is None
            or parsed_descriptor.mcp_server_configuration_version is None
            or parsed_descriptor.mcp_tool_configuration_version is None
            or "mcp_requires_credentials" not in descriptor
            or "mcp_credential_references" not in descriptor
            or "mcp_blocked_reasons" not in descriptor
        ):
            self._block(request, "mcp_tool_snapshot_invalid")
        if parsed_descriptor.mcp_blocked_reasons:
            self._block(request, "mcp_tool_snapshot_not_execution_ready")
        return parsed_descriptor, parameters, tuple(locked)

    def _validated_arguments(
        self,
        request: McpExecutionRequest,
        *,
        descriptor: CapabilityToolDescriptor,
        parameters: dict[str, object],
        locked: tuple[str, ...],
    ) -> dict[str, object]:
        arguments = dict(parameters)
        for field, value in request.arguments.items():
            if field in locked and arguments.get(field) != value:
                self._block(request, "mcp_tool_parameter_locked")
            arguments[field] = value
        try:
            validate_parameters(
                arguments,
                descriptor.input_schema,
                label="MCP tool arguments",
            )
        except ValueError:
            self._block(request, "mcp_tool_arguments_invalid")
        return arguments

    def _require_server_health(self, request: McpExecutionRequest, server: McpServer) -> None:
        if server.health_status != "healthy":
            reason = (
                "mcp_server_unhealthy"
                if server.health_status == "unhealthy"
                else "mcp_server_health_unready"
            )
            self._block(request, reason, mcp_server_id=server.id)
        if server.last_health_check_at is None:
            self._block(request, "mcp_server_health_check_missing", mcp_server_id=server.id)
        stale_after = timedelta(seconds=self.settings.mcp_health_check_stale_after_seconds)
        if mcp_health_check_stale(server, stale_after=stale_after):
            self._block(request, "mcp_server_health_check_stale", mcp_server_id=server.id)

    def _resolve_credentials(
        self,
        request: McpExecutionRequest,
        descriptor: CapabilityToolDescriptor,
        server: McpServer,
    ) -> tuple[McpCredentialReference, ...]:
        if descriptor.mcp_requires_credentials != mcp_server_requires_credentials(server):
            self._block(request, "mcp_server_configuration_stale", mcp_server_id=server.id)

        bindings = descriptor.mcp_credential_references
        seen_ids: set[UUID] = set()
        for binding in bindings:
            if binding.credential_reference_id in seen_ids:
                self._block(request, "mcp_tool_snapshot_invalid", mcp_server_id=server.id)
            seen_ids.add(binding.credential_reference_id)

        if descriptor.mcp_requires_credentials and not bindings:
            self._block(request, "mcp_credentials_required", mcp_server_id=server.id)
        if not bindings:
            return ()

        current = {
            credential.id: credential
            for credential in self.session.scalars(
                select(McpCredentialReference).where(
                    McpCredentialReference.workspace_id == request.workspace_id,
                    McpCredentialReference.id.in_(seen_ids),
                    McpCredentialReference.status == "active",
                )
            )
        }
        resolved: list[McpCredentialReference] = []
        for binding in bindings:
            credential = current.get(binding.credential_reference_id)
            if credential is None:
                self._block(request, "mcp_credential_unavailable", mcp_server_id=server.id)
            if (
                binding.mcp_server_id not in (None, server.id)
                or credential.mcp_server_id != binding.mcp_server_id
                or credential.configuration_version != binding.configuration_version
                or credential.provider != binding.provider
                or list(credential.scopes) != binding.scopes
                or credential.secret_fingerprint != binding.secret_fingerprint
                or credential.encryption_key_id != binding.encryption_key_id
            ):
                self._block(
                    request,
                    "mcp_credential_binding_stale",
                    mcp_server_id=server.id,
                )
            resolved.append(credential)
        return tuple(resolved)

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
