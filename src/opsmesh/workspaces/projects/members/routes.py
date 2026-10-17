"""Project member and invitation APIs.

Member operations live under one project; the received list and the
acceptance live under the current user, because the invitee holds no
project seat yet and has no project scope to authorize against.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.workspaces.projects.members import service
from opsmesh.workspaces.projects.members.models import ProjectInvitation, ProjectMember
from opsmesh.workspaces.projects.members.schemas import (
    InvitationCreateRequest,
    MemberRoleUpdateRequest,
    ProjectInvitationPage,
    ProjectInvitationView,
    ProjectMemberPage,
    ProjectMemberView,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/projects/{project_id}/members", tags=["workspaces"]
)
invitation_router = APIRouter(prefix="/workspaces/projects/invitations", tags=["workspaces"])

WorkspaceIdPath = Annotated[UUID, Path()]
ProjectIdPath = Annotated[UUID, Path()]
UserIdPath = Annotated[UUID, Path()]
InvitationIdPath = Annotated[UUID, Path()]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _member_view(member: ProjectMember) -> ProjectMemberView:
    return ProjectMemberView(
        project_id=member.project_id,
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        role=member.role,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def _invitation_view(invitation: ProjectInvitation) -> ProjectInvitationView:
    return ProjectInvitationView(
        invitation_id=invitation.id,
        workspace_id=invitation.workspace_id,
        project_id=invitation.project_id,
        invitee_user_id=invitation.invitee_id,
        invited_by_user_id=invitation.invited_by_user_id,
        role=invitation.role,
        state=invitation.effective_state(),
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


@router.get("", response_model=ProjectMemberPage, operation_id="workspaces.projects.members.list")
def list_members(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> ProjectMemberPage:
    """List the members of one project."""
    members, total = service.list_members(
        session,
        actor=user,
        workspace_id=workspace_id,
        project_id=project_id,
        page=PageParams(limit=limit, offset=offset),
    )
    return ProjectMemberPage(members=[_member_view(item) for item in members], total=total)


@router.patch(
    "/{user_id}",
    response_model=ProjectMemberView,
    operation_id="workspaces.projects.members.update_role",
)
def update_member_role(
    payload: MemberRoleUpdateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
    user_id: UserIdPath,
) -> ProjectMemberView:
    """Change one member's project role."""
    with session_transaction(session):
        member = service.update_member_role(
            session,
            actor=user,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            role=payload.role,
        )
    return _member_view(member)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="workspaces.projects.members.remove",
)
def remove_member(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
    user_id: UserIdPath,
) -> None:
    """Remove one project member, or leave the project as that member."""
    with session_transaction(session):
        service.remove_member(
            session,
            actor=user,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
        )


@router.post(
    "/invitations",
    response_model=ProjectInvitationView,
    status_code=status.HTTP_201_CREATED,
    operation_id="workspaces.projects.invitations.create",
)
def create_invitation(
    payload: InvitationCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
) -> ProjectInvitationView:
    """Invite one workspace member to the project under a stated role."""
    with session_transaction(session):
        invitation = service.create_invitation(
            session,
            actor=user,
            workspace_id=workspace_id,
            project_id=project_id,
            invitee_user_id=payload.invitee_user_id,
            role=payload.role,
            ttl_seconds=payload.ttl_seconds,
        )
    return _invitation_view(invitation)


@router.get(
    "/invitations",
    response_model=ProjectInvitationPage,
    operation_id="workspaces.projects.invitations.list",
)
def list_invitations(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> ProjectInvitationPage:
    """List the invitations of one project."""
    invitations, total = service.list_invitations(
        session,
        actor=user,
        workspace_id=workspace_id,
        project_id=project_id,
        page=PageParams(limit=limit, offset=offset),
    )
    return ProjectInvitationPage(
        invitations=[_invitation_view(item) for item in invitations], total=total
    )


@router.delete(
    "/invitations/{invitation_id}",
    response_model=ProjectInvitationView,
    operation_id="workspaces.projects.invitations.revoke",
)
def revoke_invitation(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    project_id: ProjectIdPath,
    invitation_id: InvitationIdPath,
) -> ProjectInvitationView:
    """Revoke one pending project invitation; a decided one is never rewritten."""
    with session_transaction(session):
        invitation = service.revoke_invitation(
            session,
            actor=user,
            workspace_id=workspace_id,
            project_id=project_id,
            invitation_id=invitation_id,
        )
    return _invitation_view(invitation)


@invitation_router.get(
    "/received",
    response_model=ProjectInvitationPage,
    operation_id="workspaces.projects.invitations.received",
)
def list_received_invitations(
    session: DbSession,
    user: CurrentUserDep,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> ProjectInvitationPage:
    """List the project invitations addressed to the current user."""
    invitations, total = service.list_received_invitations(
        session, actor=user, page=PageParams(limit=limit, offset=offset)
    )
    return ProjectInvitationPage(
        invitations=[_invitation_view(item) for item in invitations], total=total
    )


@invitation_router.post(
    "/{invitation_id}/accept",
    response_model=ProjectMemberView,
    status_code=status.HTTP_201_CREATED,
    operation_id="workspaces.projects.invitations.accept",
)
def accept_invitation(
    session: DbSession,
    user: CurrentUserDep,
    invitation_id: InvitationIdPath,
) -> ProjectMemberView:
    """Accept one project invitation addressed to the current user and join."""
    with session_transaction(session):
        member = service.accept_invitation(session, actor=user, invitation_id=invitation_id)
    return _member_view(member)
