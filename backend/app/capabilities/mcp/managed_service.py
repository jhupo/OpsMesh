from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.capabilities.mcp.catalog.contracts import (
    McpCredentialReferenceCreateRequest,
    McpServerCreateRequest,
)
from backend.app.capabilities.mcp.catalog.credentials import McpCredentialService
from backend.app.capabilities.mcp.catalog.servers import McpServerService
from backend.app.capabilities.mcp.managed_schemas import ManagedMcpCreateRequest
from backend.app.capabilities.mcp.models import McpDeployment
from backend.app.governance.audit.service import AuditService
from backend.app.governance.policies.reader import PlatformPolicyService
from backend.app.identity.authorization.context import AuthenticatedUser
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.permissions import WorkspaceAction
from backend.app.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.identity.authorization.service import AuthorizationService
from backend.app.runtime.instances.policies.safety import RuntimeSafetyPolicy
from backend.app.runtime.instances.policies.templates import RuntimeTemplateGuard
from backend.app.runtime.queues.contracts import JobPayload, JobType
from backend.app.runtime.queues.service import RedisQueue
from backend.app.shared.config import Settings
from backend.app.shared.db.errors import commit_or_raise_conflict
from backend.app.shared.errors import ConflictError, NotFoundError
from backend.app.shared.security.secrets import SecretEncryptionService


def authorize_management(session: Session, workspace_id: UUID, user: AuthenticatedUser) -> None:
    for action in (WorkspaceAction.MANAGE_CAPABILITY, WorkspaceAction.MANAGE_RUNTIME):
        AuthorizationService(session).require_workspace(
            user_id=user.user_id,
            workspace_id=workspace_id,
            action=action,
            authenticated_user=user,
        )


def deployment_job(deployment: McpDeployment) -> JobPayload:
    key = f"mcp-process:{deployment.id}:{deployment.generation}"
    return JobPayload(
        job_id=uuid5(NAMESPACE_URL, key),
        workspace_id=deployment.workspace_id,
        resource_id=deployment.id,
        job_type=JobType.MCP_PROCESS_CONTROL,
        idempotency_key=key,
        routing={"generation": deployment.generation},
    )


class ManagedMcpService:
    def __init__(self, session: Session, settings: Settings, queue: RedisQueue) -> None:
        self.session = session
        self.settings = settings
        self.queue = queue

    def create(
        self,
        workspace_id: UUID,
        user: AuthenticatedUser,
        request: ManagedMcpCreateRequest,
    ) -> list[McpDeployment]:
        authorize_management(self.session, workspace_id, user)
        identity = ExecutionIdentityService(self.session).capture(workspace_id, user.user_id)
        guard = RuntimeTemplateGuard(
            self.session,
            RuntimeSafetyPolicy(
                tuple(self.settings.runtime_allowed_images),
                PlatformPolicyService(self.session).risky_execution_policy(),
            ),
        )
        if (
            guard.validated_template(
                workspace_id=workspace_id,
                template_id=request.template_id,
                limits=None,
                network_disabled=request.network_disabled,
                runtime_space_id=None,
            )
            is None
        ):
            raise NotFoundError("Runtime template not found")
        secrets = SecretEncryptionService(
            secret=self.settings.credential_encryption_secret,
            key_id=self.settings.credential_encryption_key_id,
            previous_secrets=self.settings.credential_encryption_previous_secrets,
        )
        deployments = []
        try:
            for name, config in request.mcpServers.items():
                server = McpServerService(self.session, self.settings).create_mcp_server(
                    workspace_id,
                    McpServerCreateRequest(
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
                    credential = McpCredentialService(
                        self.session, secrets, self.settings
                    ).create_credential_reference(
                        workspace_id,
                        McpCredentialReferenceCreateRequest(
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
                deployment = McpDeployment(
                    workspace_id=workspace_id,
                    mcp_server_id=server.id,
                    template_id=request.template_id,
                    network_disabled=request.network_disabled,
                    status="queued" if approved else "pending_approval",
                    action="start",
                    generation=1,
                    execution_identity=identity,
                )
                self.session.add(deployment)
                deployments.append(deployment)
            commit_or_raise_conflict(self.session, "MCP project name already exists")
        except Exception:
            self.session.rollback()
            raise
        for deployment in deployments:
            if deployment.status == "queued":
                self._enqueue(deployment)
        return deployments

    def get(self, workspace_id: UUID, server_id: UUID, *, lock: bool = False) -> McpDeployment:
        query = select(McpDeployment).where(
            McpDeployment.workspace_id == workspace_id,
            McpDeployment.mcp_server_id == server_id,
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
    ) -> McpDeployment:
        authorize_management(self.session, workspace_id, user)
        deployment = self.get(workspace_id, server_id, lock=True)
        ResourceAuthorizationService(self.session, user).require(
            workspace_id,
            ResourceKind.MCP_SERVER,
            server_id,
            ResourceAction.CONTROL,
        )
        if deployment.status == "starting":
            raise ConflictError("MCP lifecycle operation is in progress")
        if deployment.status == "queued" and deployment.action == action:
            self._enqueue(deployment)
            return deployment
        deployment.execution_identity = ExecutionIdentityService(self.session).capture(
            workspace_id, user.user_id
        )
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
        self.session.commit()
        self._enqueue(deployment)
        return deployment

    def _enqueue(self, deployment: McpDeployment) -> None:
        try:
            self.queue.ensure_enqueued(deployment_job(deployment))
        except Exception:
            # Intent is durable. A repeated lifecycle request or maintenance repairs delivery.
            deployment.last_error = "mcp_process_enqueue_failed"
            self.session.commit()
