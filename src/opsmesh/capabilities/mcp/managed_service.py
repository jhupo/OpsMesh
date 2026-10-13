from __future__ import annotations

from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.catalog.contracts import (
    MCPCredentialReferenceCreateRequest,
    MCPServerCreateRequest,
)
from opsmesh.capabilities.mcp.catalog.credentials import MCPCredentialService
from opsmesh.capabilities.mcp.catalog.servers import MCPServerService
from opsmesh.capabilities.mcp.managed_runtime import require_execution_environment
from opsmesh.capabilities.mcp.managed_schemas import ManagedMCPCreateRequest
from opsmesh.capabilities.mcp.models import MCPDeployment
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.config import Settings
from opsmesh.shared.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.security.secrets import SecretEncryptionService


def authorize_management(session: Session, workspace_id: UUID, user: AuthenticatedUser) -> None:
    for action in (WorkspaceAction.MANAGE_CAPABILITY, WorkspaceAction.MANAGE_RUNTIME):
        WorkspaceAuthorizationService(session).require_workspace(
            user_id=user.user_id,
            workspace_id=workspace_id,
            action=action,
            authenticated_user=user,
        )


class ManagedMCPService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def create(
        self,
        workspace_id: UUID,
        user: AuthenticatedUser,
        request: ManagedMCPCreateRequest,
    ) -> list[MCPDeployment]:
        authorize_management(self.session, workspace_id, user)
        require_execution_admission(self.session)
        require_execution_environment(self.session, workspace_id, request.runtime_id)
        ResourceAuthorizationService(self.session, user).require(
            workspace_id,
            ResourceKind.RUNTIME,
            request.runtime_id,
            ResourceAction.INVOKE,
        )
        secrets = SecretEncryptionService(
            secret=self.settings.credential_encryption_secret,
            key_id=self.settings.credential_encryption_key_id,
            previous_secrets=self.settings.credential_encryption_previous_secrets,
        )
        deployments = []
        try:
            for name, config in request.mcp_servers.items():
                server = MCPServerService(self.session, self.settings).create_mcp_server(
                    workspace_id,
                    MCPServerCreateRequest(
                        name=name,
                        server_type="stdio",
                        connection={
                            **config.model_dump(exclude_none=True),
                            "runtime": "managed",
                            "requires_credentials": bool(config.env),
                        },
                    ),
                    user.user_id,
                    commit=False,
                )
                approved = server.status == "active"
                if config.env:
                    credential = MCPCredentialService(
                        self.session, secrets, self.settings
                    ).create_credential_reference(
                        workspace_id,
                        MCPCredentialReferenceCreateRequest(
                            mcp_server_id=server.id,
                            name=f"env-{server.id}",
                            provider="hosted",
                            secret_payload={"env": config.env},
                        ),
                        user.user_id,
                        commit=False,
                    )
                    approved = approved and credential.status == "active"
                    server.connection = {
                        **server.connection,
                        "credential_reference_id": str(credential.id),
                    }
                deployment = MCPDeployment(
                    workspace_id=workspace_id,
                    mcp_server_id=server.id,
                    runtime_id=request.runtime_id,
                    status="queued" if approved else "pending_approval",
                    action="start",
                    generation=1,
                )
                self.session.add(deployment)
                deployments.append(deployment)
            flush_or_raise_conflict(self.session, "MCP project name already exists")
            for deployment in deployments:
                if deployment.status == "queued":
                    self._stage(deployment, user.user_id)
            commit_or_raise_conflict(self.session, "MCP project name already exists")
        except Exception:
            self.session.rollback()
            raise
        return deployments

    def get(self, workspace_id: UUID, server_id: UUID, *, lock: bool = False) -> MCPDeployment:
        query = select(MCPDeployment).where(
            MCPDeployment.workspace_id == workspace_id,
            MCPDeployment.mcp_server_id == server_id,
        )
        deployment = self.session.scalar(query.with_for_update() if lock else query)
        if deployment is None:
            raise NotFoundError("Managed MCP deployment not found")
        return deployment

    def control(
        self,
        workspace_id: UUID,
        server_id: UUID,
        user: AuthenticatedUser,
        action: str,
    ) -> MCPDeployment:
        authorize_management(self.session, workspace_id, user)
        deployment = self.get(workspace_id, server_id, lock=True)
        ResourceAuthorizationService(self.session, user).require(
            workspace_id,
            ResourceKind.MCP_SERVER,
            server_id,
            ResourceAction.CONTROL,
        )
        if deployment.status == "starting" and action != "stop":
            raise ConflictError("MCP lifecycle operation is in progress")
        if deployment.status == "queued" and deployment.action == action:
            self._stage(deployment, user.user_id)
            self.session.commit()
            return deployment
        if action != "stop":
            require_execution_admission(self.session)
        deployment.action = action
        deployment.status = "queued"
        deployment.generation += 1
        deployment.last_error = None
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=user.user_id,
            action=f"mcp_process.{action}_requested",
            target_type="mcp_server",
            target_id=server_id,
            metadata={"generation": deployment.generation},
        )
        self._stage(deployment, user.user_id)
        self.session.commit()
        return deployment

    def bind_environment(
        self, workspace_id: UUID, server_id: UUID, user: AuthenticatedUser, runtime_id: UUID
    ) -> MCPDeployment:
        authorize_management(self.session, workspace_id, user)
        deployment = self.get(workspace_id, server_id, lock=True)
        authorization = ResourceAuthorizationService(self.session, user)
        authorization.require(
            workspace_id, ResourceKind.MCP_SERVER, server_id, ResourceAction.CONTROL
        )
        environment = require_execution_environment(self.session, workspace_id, runtime_id)
        authorization.require(
            workspace_id, ResourceKind.RUNTIME, environment.id, ResourceAction.INVOKE
        )
        if deployment.status not in {"stopped", "failed", "pending_approval"}:
            raise ConflictError("Stop the MCP deployment before changing its execution environment")
        deployment.runtime_id = environment.id
        deployment.server_version = None
        deployment.credential_version = None
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=user.user_id,
            action="mcp_process.environment_bound",
            target_type="mcp_server",
            target_id=server_id,
            metadata={"runtime_id": str(environment.id)},
        )
        self.session.commit()
        return deployment

    def _stage(self, deployment: MCPDeployment, user_id: UUID) -> None:
        stage_execution(
            self.session,
            ExecutionRequest(
                execution_id=uuid5(
                    deployment.id, f"control:{deployment.generation}:{deployment.action}"
                ),
                workspace_id=deployment.workspace_id,
                resource_id=deployment.id,
                kind=ExecutionKind.MCP_CONTROL,
                requested_by_user_id=user_id,
                parameters={
                    "action": deployment.action,
                    "deployment_version": deployment.generation,
                },
            ),
        )
