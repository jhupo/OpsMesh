"""Register feature routers and assemble the governance and delegation identities.

Governance modules never import identity: this assembly point replaces their
placeholder dependencies with the real identity channels, and resolves the
restricted delegation subject that extension-reserved operations require.
The workspace step registers the real scope owners here: the resource and
audit scope admissions delegate to the one space and project access paths,
so no module keeps a fixed allow or deny callback of its own.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Path
from sqlalchemy.orm import Session

from opsmesh.capabilities.experts import routes as expert_routes
from opsmesh.capabilities.mcp import routes as mcp_routes
from opsmesh.capabilities.routes import router as capability_router
from opsmesh.capabilities.skills import routes as skill_routes
from opsmesh.capabilities.teams import routes as team_routes
from opsmesh.capabilities.tools import routes as tool_routes
from opsmesh.governance.approvals import routes as approval_routes
from opsmesh.governance.audit import routes as audit_routes
from opsmesh.governance.audit.service import AuditActor
from opsmesh.governance.costs import routes as cost_routes
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
from opsmesh.resources.files.access import ResourceReader, ScopeRef
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.workspaces import routes as workspace_routes
from opsmesh.workspaces.capacity import routes as capacity_routes
from opsmesh.workspaces.credentials import routes as credential_routes
from opsmesh.workspaces.members import access as space_access
from opsmesh.workspaces.members import routes as member_routes
from opsmesh.workspaces.members.models import SPACE_ROLE_ADMIN, SPACE_ROLE_OWNER
from opsmesh.workspaces.model_providers import routes as model_provider_routes
from opsmesh.workspaces.notifications import routes as notification_routes
from opsmesh.workspaces.projects import routes as project_routes
from opsmesh.workspaces.projects.members import access as project_access
from opsmesh.workspaces.projects.members import routes as project_member_routes


class RejectingMailAuthorization:
    """No delegated mail source is registered before the producers (S8)."""

    def authorize(self, session: Session, scope: SourceScope) -> None:
        _ = session
        raise ForbiddenError(
            "The mail source is not registered", details={"source": scope.source}
        )


class WorkspaceScopeAdmission:
    """The real resource scope admission: space and project membership.

    The user scope needs no external owner — the file-level owner and grant
    checks authorize it. A workspace scope requires membership of that
    workspace, a project scope a project role, which space leadership holds
    everywhere and everyone else holds seat by seat. A project scope without
    its workspace is malformed and refused.
    """

    def admit(self, session: Session, *, reader: ResourceReader, scope: ScopeRef) -> None:
        if scope.is_user_scope:
            return
        workspace_id = scope.workspace_id
        if workspace_id is None:
            raise ForbiddenError(
                "The resource scope is not admitted",
                details={"project_id": str(scope.project_id) if scope.project_id else None},
            )
        if scope.project_id is None:
            admitted = space_access.space_role(
                session, workspace_id=workspace_id, user_id=reader.user_id
            )
        else:
            admitted = project_access.project_role(
                session,
                workspace_id=workspace_id,
                project_id=scope.project_id,
                user_id=reader.user_id,
            )
        if admitted is None:
            raise ForbiddenError(
                "The resource scope is not admitted",
                details={
                    "workspace_id": str(workspace_id),
                    "project_id": str(scope.project_id) if scope.project_id else None,
                },
            )


class WorkspaceAuditScopeAdmission:
    """The real audit scope admission, resolved through the same access paths."""

    def admit(
        self,
        session: Session,
        *,
        user_id: UUID,
        workspace_id: UUID | None,
        project_id: UUID | None,
    ) -> None:
        admitted = False
        if project_id is not None and workspace_id is not None:
            admitted = (
                project_access.project_role(
                    session,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    user_id=user_id,
                )
                is not None
            )
        elif workspace_id is not None:
            admitted = (
                space_access.space_role(session, workspace_id=workspace_id, user_id=user_id)
                is not None
            )
        if not admitted:
            raise ForbiddenError(
                "The audit scope is not admitted",
                details={
                    "workspace_id": str(workspace_id) if workspace_id else None,
                    "project_id": str(project_id) if project_id else None,
                },
            )


def _audit_reader(user: CurrentUserDep) -> CurrentUser:
    """Audit reads are authorized per scope by the audit routes themselves."""
    return user


class WorkspaceBudgetScopeAdmission:
    """The real budget scope admission: the workspace's owner or admin.

    A platform administrator gets no tenant data permission from this
    path — pricing administration is the only platform-level cost
    surface, and a workspace's budget and usage stay with its own
    leadership.
    """

    def admit(self, session: Session, *, user_id: UUID, workspace_id: UUID) -> None:
        role = space_access.space_role(session, workspace_id=workspace_id, user_id=user_id)
        if role not in (SPACE_ROLE_OWNER, SPACE_ROLE_ADMIN):
            raise ForbiddenError(
                "The budget scope is not admitted",
                details={"workspace_id": str(workspace_id)},
            )


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


def _cost_admin_subject(user: CurrentUserDep) -> CurrentUser:
    """Pricing administration is the platform-level cost surface."""
    require_platform_admin(user)
    return user


def _budget_subject(
    user: CurrentUserDep,
    session: Annotated[Session, Depends(get_db_session)],
    workspace_id: Annotated[UUID, Path()],
) -> CurrentUser:
    """The verified session identity, admitted to the workspace's budget scope."""
    WorkspaceBudgetScopeAdmission().admit(
        session, user_id=user.user_id, workspace_id=workspace_id
    )
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
        cost_routes.router,
        security_routes.router,
        mail_routes.router,
        capability_router,
        expert_routes.router,
        team_routes.router,
        mcp_routes.router,
        skill_routes.router,
        tool_routes.router,
        file_routes.router,
        artifact_routes.router,
        workspace_routes.router,
        capacity_routes.router,
        credential_routes.router,
        member_routes.router,
        member_routes.invitation_router,
        model_provider_routes.router,
        notification_routes.router,
        project_routes.router,
        project_member_routes.router,
        project_member_routes.invitation_router,
    ):
        app.include_router(router, prefix=prefix)
    app.dependency_overrides[audit_routes.require_audit_reader] = _audit_reader
    app.dependency_overrides[audit_routes.require_extension_actor] = _extension_audit_actor
    app.dependency_overrides[approval_routes.require_approval_subject] = _approval_subject
    app.dependency_overrides[cost_routes.require_admin_subject] = _cost_admin_subject
    app.dependency_overrides[cost_routes.require_budget_subject] = _budget_subject
    app.dependency_overrides[security_routes.require_security_reader] = _security_reader
    app.dependency_overrides[mail_routes.require_delegated_mail_send] = _delegated_mail_send
    app.dependency_overrides[file_routes.require_resource_reader] = _resource_reader
    app.dependency_overrides[artifact_routes.require_resource_reader] = _resource_reader
