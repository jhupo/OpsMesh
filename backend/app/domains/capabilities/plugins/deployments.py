"""Admission and durable intent for isolated, platform-managed plugin processes."""

import re
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.execution import ExecutionIdentityService
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.access.resources import ResourceAccessDenied
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.capabilities.plugins.models import PluginDeployment
from backend.app.domains.capabilities.plugins.services import SERVICE_PERMISSIONS, PluginServices
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.environment.contracts import RuntimeLimits
from backend.app.runtime.environment.models import RuntimeTemplate
from backend.app.runtime.environment.policies.runtime import RuntimePolicyResolver


def deployment_lock_key(install_id: UUID) -> int:
    return int.from_bytes(install_id.bytes[:8], "big", signed=True)


def lock_deployment(session: Session, install_id: UUID) -> None:
    if not session.scalar(select(func.pg_try_advisory_xact_lock(deployment_lock_key(install_id)))):
        raise DomainError("Deployment is reconciling", code="deployment_busy", status_code=409)


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    template_id: UUID
    platform_url: str = Field(max_length=2048)
    permissions: list[str] = Field(min_length=1, max_length=32)
    environment: dict[str, SecretStr] = Field(default_factory=dict, max_length=32, repr=False)
    desired_state: Literal["running", "stopped"] = "running"

    @model_validator(mode="after")
    def validate_process(self) -> "DeploymentRequest":
        url = urlsplit(self.platform_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not url.path.endswith("/api/v1/")
        ):
            raise ValueError("Use a credential-free HTTPS platform API root")
        if not set(self.permissions) <= SERVICE_PERMISSIONS:
            raise ValueError("Unsupported service permission")
        for key, value in self.environment.items():
            if (
                not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key)
                or key.startswith(("OPSMESH_", "LD_", "PYTHON"))
                or key in {"PATH", "HOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
                or len(value.get_secret_value()) > 8192
                or "\x00" in value.get_secret_value()
            ):
                raise ValueError("Invalid process environment")
        return self


class DeploymentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    install_id: UUID
    template_id: UUID
    image: str
    desired_state: str
    status: str
    revision: int
    applied_revision: int
    runtime_id: UUID | None
    attempts: int
    error_code: str | None
    next_check_at: datetime


class DeploymentConfiguration(BaseModel):
    platform_url: str
    permissions: list[str]
    limits: RuntimeLimits
    network_policy: dict[str, object]


class PluginDeploymentService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, workspace_id: UUID, install_id: UUID) -> PluginDeployment:
        item = self.session.scalar(
            select(PluginDeployment).where(
                PluginDeployment.workspace_id == workspace_id,
                PluginDeployment.install_id == install_id,
            )
        )
        if item is None:
            raise DomainError("Deployment not found", code="deployment_missing", status_code=404)
        return item

    def configure(
        self,
        workspace_id: UUID,
        install_id: UUID,
        actor: AuthenticatedUser,
        request: DeploymentRequest,
        secrets: SecretEncryptionService,
    ) -> PluginDeployment:
        AuthorizationService(self.session).require_workspace(
            user_id=actor.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        services = PluginServices(self.session)
        lock_deployment(self.session, install_id)
        install = services.active_install(workspace_id, install_id, lock=True)
        release = services.active_release(install)
        if not set(request.permissions) <= set(release.approved_permissions):
            raise ResourceAccessDenied()
        template = self.session.get(RuntimeTemplate, request.template_id)
        if (
            template is None
            or template.status != "active"
            or not re.fullmatch(r"[a-z0-9./:_-]+@sha256:[a-f0-9]{64}", template.image)
        ):
            raise ValueError("An active, digest-pinned runtime template is required")
        item = self.session.scalar(
            select(PluginDeployment)
            .where(
                PluginDeployment.workspace_id == workspace_id,
                PluginDeployment.install_id == install_id,
            )
            .with_for_update()
        )
        if request.expected_revision != (item.revision if item else 0):
            raise DomainError("Deployment changed", code="deployment_conflict", status_code=409)
        if item is None:
            item = PluginDeployment(workspace_id=workspace_id, install_id=install_id, revision=0)
        encrypted = secrets.encrypt_payload(
            {k: v.get_secret_value() for k, v in request.environment.items()}
        )
        item.template_id, item.image = template.id, template.image
        item.generation = install.generation
        item.execution_identity = ExecutionIdentityService(self.session).capture(
            workspace_id, actor.user_id
        )
        policy = RuntimePolicyResolver(self.session).resolve_runtime_policy(
            workspace_id=workspace_id,
            runtime_space_id=None,
            template=template,
            requested_limits=None,
            requested_network_disabled=False,
        )
        effective = policy.metadata["effective"]
        network = effective["egress"] if isinstance(effective, dict) else None
        if not isinstance(network, dict) or policy.network_disabled:
            raise ValueError("Plugin runtime template must explicitly allow its network access")
        item.configuration = DeploymentConfiguration(
            platform_url=request.platform_url,
            permissions=sorted(set(request.permissions)),
            limits=policy.limits,
            network_policy={"disabled": False, **network},
        ).model_dump(mode="json")
        item.encrypted_environment, item.encryption_key_id = encrypted.ciphertext, encrypted.key_id
        item.desired_state, item.status = request.desired_state, "pending"
        item.revision += 1
        item.attempts, item.error_code = 0, None
        item.next_check_at = datetime.now(UTC)
        self.session.add(item)
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor.user_id,
            action="plugin.deployment.configured",
            target_type="plugin_install",
            target_id=install_id,
            metadata={
                "revision": item.revision,
                "image": item.image,
                "desired_state": item.desired_state,
            },
        )
        self.session.commit()
        self.session.refresh(item)
        return item

    def stop(
        self, workspace_id: UUID, install_id: UUID, actor: AuthenticatedUser
    ) -> PluginDeployment:
        AuthorizationService(self.session).require_workspace(
            user_id=actor.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        lock_deployment(self.session, install_id)
        item = self.get(workspace_id, install_id)
        item.desired_state, item.status = "stopped", "pending"
        item.revision += 1
        item.attempts, item.error_code = 0, None
        item.next_check_at = datetime.now(UTC)
        AuditService(self.session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor.user_id,
            action="plugin.deployment.stop_requested",
            target_type="plugin_install",
            target_id=install_id,
        )
        self.session.commit()
        return item
