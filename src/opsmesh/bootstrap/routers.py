"""Register feature routers and assemble the governance and delegation identities.

Governance modules never import identity: this assembly point replaces their
placeholder dependencies with the real identity channels, and resolves the
restricted delegation subject that extension-reserved operations require.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header
from sqlalchemy.orm import Session

from opsmesh.capabilities.channels import routes as channel_routes
from opsmesh.capabilities.experts import routes as expert_routes
from opsmesh.capabilities.extensions import routes as extension_routes
from opsmesh.capabilities.mcp import routes as mcp_routes
from opsmesh.capabilities.routes import router as capability_router
from opsmesh.capabilities.skills import routes as skill_routes
from opsmesh.capabilities.teams import routes as team_routes
from opsmesh.capabilities.tools import routes as tool_routes
from opsmesh.governance.approvals import routes as approval_routes
from opsmesh.governance.audit import routes as audit_routes
from opsmesh.governance.audit.service import AuditActor
from opsmesh.governance.security_events import routes as security_routes
from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import (
    INSTALLATION_HEADER,
    INSTANCE_HEADER,
    INVOCATION_HEADER,
    NAMESPACE_HEADER,
    CurrentUserDep,
    DelegatedUserDep,
)
from opsmesh.identity.auth.routes import router as auth_router
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.errors import AuthenticationError
from opsmesh.identity.invitations.routes import router as invitation_router
from opsmesh.identity.quotas.routes import router as quota_router
from opsmesh.identity.users.routes import router as user_router
from opsmesh.platform.mail import routes as mail_routes
from opsmesh.platform.mail.service import (
    DelegatedMailSend,
    MailSource,
    SourceScope,
)
from opsmesh.resources.artifacts import routes as artifact_routes
from opsmesh.resources.files import routes as file_routes
from opsmesh.resources.files.access import ResourceReader
from opsmesh.shared.errors import ForbiddenError


class RejectingMailAuthorization:
    """No delegated mail source is registered before the producers (S8)."""

    def authorize(self, session: Session, scope: SourceScope) -> None:
        _ = session
        raise ForbiddenError(
            "The mail source is not registered", details={"source": scope.source}
        )


def _audit_reader(user: CurrentUserDep) -> CurrentUser:
    """Audit reads are platform-administrator only; space roles never qualify."""
    require_platform_admin(user)
    return user


def _extension_audit_actor(
    user: DelegatedUserDep,
    x_installation_id: Annotated[str | None, Header(alias=INSTALLATION_HEADER)],
    x_instance_id: Annotated[str | None, Header(alias=INSTANCE_HEADER)],
    x_invocation_id: Annotated[str | None, Header(alias=INVOCATION_HEADER)],
    x_namespace: Annotated[str | None, Header(alias=NAMESPACE_HEADER)],
) -> AuditActor:
    """The verified delegation is the only actor an extension append may claim."""
    return AuditActor(
        actor_type="delegation",
        actor_id=str(user.user_id),
        installation_id=_uuid(x_installation_id, INSTALLATION_HEADER),
        instance_id=_uuid(x_instance_id, INSTANCE_HEADER),
        invocation_id=_uuid(x_invocation_id, INVOCATION_HEADER),
        package_key=x_namespace,
    )


def _approval_subject(user: CurrentUserDep) -> CurrentUser:
    return user


def _security_reader(user: CurrentUserDep) -> CurrentUser:
    require_platform_admin(user)
    return user


def _resource_reader(user: CurrentUserDep) -> ResourceReader:
    """The verified session identity is the only resource reader."""
    return ResourceReader(user_id=user.user_id)


def _delegated_mail_send(
    user: DelegatedUserDep,
    x_installation_id: Annotated[str | None, Header(alias=INSTALLATION_HEADER)],
    x_instance_id: Annotated[str | None, Header(alias=INSTANCE_HEADER)],
    x_invocation_id: Annotated[str | None, Header(alias=INVOCATION_HEADER)],
    x_namespace: Annotated[str | None, Header(alias=NAMESPACE_HEADER)],
) -> DelegatedMailSend:
    """A delegated send carries its verified invocation scope as its source."""
    _ = user, x_instance_id
    return DelegatedMailSend(
        source=MailSource(
            source=f"extension:{x_namespace}",
            installation_id=_uuid(x_installation_id, INSTALLATION_HEADER),
            invocation_id=_uuid(x_invocation_id, INVOCATION_HEADER),
        ),
        authorization=RejectingMailAuthorization(),
    )


def _uuid(value: str | None, header: str) -> UUID:
    if value is None:
        raise AuthenticationError(f"The {header} header is required")
    try:
        return UUID(value)
    except ValueError as exc:
        raise AuthenticationError(f"The {header} header is malformed") from exc


def register_routers(app: FastAPI, *, prefix: str) -> None:
    """Include feature routers under the configured API prefix."""
    for router in (
        auth_router,
        user_router,
        quota_router,
        invitation_router,
        audit_routes.router,
        approval_routes.router,
        security_routes.router,
        mail_routes.router,
        capability_router,
        expert_routes.router,
        team_routes.router,
        mcp_routes.router,
        skill_routes.router,
        tool_routes.router,
        channel_routes.router,
        extension_routes.router,
        file_routes.router,
        artifact_routes.router,
    ):
        app.include_router(router, prefix=prefix)
    app.dependency_overrides[audit_routes.require_audit_reader] = _audit_reader
    app.dependency_overrides[audit_routes.require_extension_actor] = _extension_audit_actor
    app.dependency_overrides[approval_routes.require_approval_subject] = _approval_subject
    app.dependency_overrides[security_routes.require_security_reader] = _security_reader
    app.dependency_overrides[mail_routes.require_delegated_mail_send] = _delegated_mail_send
    app.dependency_overrides[file_routes.require_resource_reader] = _resource_reader
    app.dependency_overrides[artifact_routes.require_resource_reader] = _resource_reader
