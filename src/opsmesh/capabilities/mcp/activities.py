from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.capabilities.mcp.catalog.discovery import MCPToolDiscoveryService
from opsmesh.capabilities.mcp.managed_service import authorize_management
from opsmesh.capabilities.mcp.models import MCPCredentialReference, MCPDeployment, MCPServer
from opsmesh.capabilities.mcp.sandbox_stdio import SandboxMCPServerStdio
from opsmesh.capabilities.mcp.transport.credentials import resolve_hosted_credentials
from opsmesh.capabilities.mcp.transport.payloads import stdio_command
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.sandboxes.registry import SandboxBackendRegistry
from opsmesh.runtime.sandboxes.service import SandboxService
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.errors import NotFoundError, PolicyDeniedError


@dataclass(frozen=True)
class _ManagedConnection:
    server_id: UUID
    runtime_id: UUID
    server_version: int
    credential_id: UUID | None
    credential_version: int | None
    environment: dict[str, str]
    command: tuple[str, ...]
    cwd: str | None
    name: str


class ManagedMCPActivities:
    """Managed readiness with short DB transactions and SDK-owned process lifetime."""

    def __init__(self, services: ActivityDependencies, backends: SandboxBackendRegistry) -> None:
        self._services = services
        self._backends = backends
        self._sandbox = SandboxService(services.transaction, backends, services.secrets())

    @activity.defn(name="opsmesh.mcp.control")
    async def control(self, request: ExecutionRequest) -> None:
        if request.kind != ExecutionKind.MCP_CONTROL:
            raise ValueError("MCP lifecycle activity requires an MCP control execution")
        action = request.parameters.get("action")
        version = request.parameters.get("deployment_version")
        if not isinstance(action, str) or action not in {"start", "stop", "restart", "refresh"}:
            raise ValueError("MCP lifecycle action is invalid")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("MCP deployment version is invalid")
        heartbeat = asyncio.create_task(_heartbeat())
        try:
            preparation = asyncio.create_task(
                asyncio.to_thread(self._prepare, request, action, version)
            )
            try:
                plan = await asyncio.shield(preparation)
            except asyncio.CancelledError:
                await preparation
                raise
            if plan is None:
                return
            for reference_id in await asyncio.to_thread(self._references, request, plan.server_id):
                await self._sandbox.delete(request.workspace_id, reference_id)
            tools: list[dict[str, object]] = []
            if action != "stop":
                client = SandboxMCPServerStdio(
                    session_factory=self._services.transaction,
                    backends=self._backends,
                    secrets=self._services.secrets(),
                    workspace_id=request.workspace_id,
                    runtime_id=plan.runtime_id,
                    server_id=plan.server_id,
                    command=list(plan.command),
                    environment=plan.environment,
                    cwd=plan.cwd,
                    name=plan.name,
                    timeout_seconds=self._services.settings.mcp_tool_timeout_seconds,
                )
                try:
                    await client.connect()
                    tools = [
                        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                        for tool in await client.list_tools()
                    ]
                finally:
                    await asyncio.shield(client.cleanup())
            await asyncio.to_thread(self._finish, request, action, version, plan, tools)
        except BaseException:
            await asyncio.shield(asyncio.to_thread(self._fail, request, action, version))
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    def _prepare(
        self, request: ExecutionRequest, action: str, version: int
    ) -> _ManagedConnection | None:
        with self._services.transaction() as session:
            authorize_execution(session, request, action=WorkspaceAction.MANAGE_CAPABILITY)
            deployment = self._current_deployment(session, request, action, version)
            if deployment is None:
                return None
            user = self._authenticate(session, request)
            authorize_management(session, request.workspace_id, user)
            ResourceAuthorizationService(session, user).require(
                request.workspace_id,
                ResourceKind.MCP_SERVER,
                deployment.mcp_server_id,
                ResourceAction.CONTROL,
            )
            runtime = session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == request.workspace_id,
                    WorkspaceRuntime.id == deployment.runtime_id,
                )
            )
            server = session.scalar(
                select(MCPServer).where(
                    MCPServer.workspace_id == request.workspace_id,
                    MCPServer.id == deployment.mcp_server_id,
                )
            )
            if runtime is None or server is None:
                raise NotFoundError("Managed MCP resources are unavailable")
            if action != "stop":
                ResourceAuthorizationService(session, user).require(
                    request.workspace_id, ResourceKind.RUNTIME, runtime.id, ResourceAction.INVOKE
                )
                self._validate_start(session, request, runtime, server)
            credential = (
                self._credential(
                    session, request, server, server.connection.get("credential_reference_id")
                )
                if action != "stop"
                else None
            )
            environment, _ = resolve_hosted_credentials(
                [credential] if credential is not None else [], self._services.secrets()
            )
            deployment.status = "stopping" if action == "stop" else "starting"
            deployment.checked_at = datetime.now(UTC)
            cwd = server.connection.get("cwd")
            return _ManagedConnection(
                server.id,
                runtime.id,
                server.configuration_version,
                credential.id if credential else None,
                credential.configuration_version if credential else None,
                environment,
                tuple(stdio_command(server.connection)) if action != "stop" else (),
                cwd if isinstance(cwd, str) else None,
                server.name,
            )

    def _finish(
        self,
        request: ExecutionRequest,
        action: str,
        version: int,
        plan: _ManagedConnection,
        tools: list[dict[str, object]],
    ) -> None:
        with self._services.transaction() as session:
            deployment = self._current_deployment(session, request, action, version)
            if deployment is None:
                return
            server = session.scalar(
                select(MCPServer).where(
                    MCPServer.workspace_id == request.workspace_id,
                    MCPServer.id == plan.server_id,
                )
            )
            if server is None:
                raise NotFoundError("Managed MCP server is unavailable")
            if action != "stop":
                MCPToolDiscoveryService(
                    session, secret_service=self._services.secrets()
                ).persist_runtime_tools(
                    request.workspace_id,
                    server,
                    tools,
                    credential_id=plan.credential_id,
                    credential_version=plan.credential_version,
                    actor_user_id=request.requested_by_user_id,
                    configuration_version=plan.server_version,
                )
                # Discovery commits its catalog transaction, so recheck the lifecycle generation.
                deployment = self._current_deployment(session, request, action, version)
                if deployment is None:
                    return
                deployment.server_version = plan.server_version
                deployment.credential_version = plan.credential_version
                server.health_status = "healthy"
                server.last_error = None
                server.last_health_check_at = deployment.checked_at
            deployment.status = "stopped" if action == "stop" else "ready"
            deployment.last_error = None
            AuditService(session).record_system_action(
                workspace_id=request.workspace_id,
                action=f"mcp_process.{deployment.status}",
                target_type="mcp_server",
                target_id=deployment.mcp_server_id,
                metadata={
                    "deployment_id": str(deployment.id),
                    "generation": version,
                    "action": action,
                },
            )

    def _fail(self, request: ExecutionRequest, action: str, version: int) -> None:
        with self._services.transaction() as session:
            deployment = self._current_deployment(session, request, action, version)
            if deployment is None:
                return
            deployment.status = "failed"
            deployment.last_error = "managed_mcp_lifecycle_failed"
            server = session.scalar(
                select(MCPServer).where(
                    MCPServer.workspace_id == request.workspace_id,
                    MCPServer.id == deployment.mcp_server_id,
                )
            )
            if server is not None:
                server.health_status = "unhealthy"
                server.last_error = deployment.last_error
            AuditService(session).record_system_action(
                workspace_id=request.workspace_id,
                action="mcp_process.failed",
                target_type="mcp_server",
                target_id=deployment.mcp_server_id,
                metadata={
                    "deployment_id": str(deployment.id),
                    "generation": version,
                    "action": action,
                },
            )

    def _references(self, request: ExecutionRequest, server_id: UUID) -> list[UUID]:
        with self._services.transaction() as session:
            return list(
                session.scalars(
                    select(SandboxReference.id).where(
                        SandboxReference.workspace_id == request.workspace_id,
                        SandboxReference.owner_kind == "mcp",
                        SandboxReference.scope_id == server_id,
                        SandboxReference.status != "deleted",
                    )
                )
            )

    @staticmethod
    def _current_deployment(
        session: Session, request: ExecutionRequest, action: str, version: int
    ) -> MCPDeployment | None:
        current = session.scalar(
            select(MCPDeployment)
            .where(
                MCPDeployment.workspace_id == request.workspace_id,
                MCPDeployment.id == request.resource_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is None or current.generation != version or current.action != action:
            return None
        return current

    @staticmethod
    def _authenticate(session: Session, request: ExecutionRequest) -> AuthenticatedUser:
        if request.requested_by_user_id is None:
            raise PolicyDeniedError("MCP lifecycle requires an authorized user")
        return AuthenticationService(session).authenticate_user(request.requested_by_user_id)

    @staticmethod
    def _validate_start(
        session: Session,
        request: ExecutionRequest,
        runtime: WorkspaceRuntime,
        server: MCPServer,
    ) -> None:
        if runtime.status != "active":
            raise PolicyDeniedError("Managed MCP environment is unavailable")
        if (
            server.status != "active"
            or server.platform_blocked
            or server.connection.get("runtime") != "managed"
        ):
            raise PolicyDeniedError("MCP project is not approved for execution")
        ManagedMCPActivities._credential(
            session, request, server, server.connection.get("credential_reference_id")
        )

    @staticmethod
    def _credential(
        session: Session,
        request: ExecutionRequest,
        server: MCPServer,
        credential_id: object,
    ) -> MCPCredentialReference | None:
        if not server.connection.get("requires_credentials"):
            return None
        try:
            parsed = UUID(str(credential_id))
        except (TypeError, ValueError) as exc:
            raise PolicyDeniedError("MCP credentials are unavailable") from exc
        credential = session.scalar(
            select(MCPCredentialReference).where(
                MCPCredentialReference.workspace_id == request.workspace_id,
                MCPCredentialReference.mcp_server_id == server.id,
                MCPCredentialReference.id == parsed,
                MCPCredentialReference.status == "active",
            )
        )
        if credential is None:
            raise PolicyDeniedError("MCP credentials are unavailable")
        return credential


async def _heartbeat() -> None:
    while True:
        activity.heartbeat()
        await asyncio.sleep(5)
