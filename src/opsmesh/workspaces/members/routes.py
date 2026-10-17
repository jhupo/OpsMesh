"""Space member and invitation APIs.

Member operations live under one workspace; the received list and the
acceptance live under the current user, because the invitee is not a
member yet and has no workspace scope to authorize against.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.workspaces.members import service
from opsmesh.workspaces.members.models import WorkspaceInvitation, WorkspaceMember
from opsmesh.workspaces.members.schemas import (
    InvitationCreateRequest,
    MemberRoleUpdateRequest,
    SpaceInvitationPage,
    SpaceInvitationView,
    SpaceMemberPage,
    SpaceMemberView,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/members", tags=["workspaces"])
invitation_router = APIRouter(prefix="/workspaces/invitations", tags=["workspaces"])

WorkspaceIdPath = Annotated[UUID, Path()]
UserIdPath = Annotated[UUID, Path()]
InvitationIdPath = Annotated[UUID, Path()]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _member_view(member: WorkspaceMember) -> SpaceMemberView:
    return SpaceMemberView(
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        role=member.role,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def _invitation_view(invitation: WorkspaceInvitation) -> SpaceInvitationView:
    return SpaceInvitationView(
        invitation_id=invitation.id,
        workspace_id=invitation.workspace_id,
        invitee_user_id=invitation.invitee_id,
        invited_by_user_id=invitation.invited_by_user_id,
        role=invitation.role,
        state=invitation.effective_state(),
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


@router.get("", response_model=SpaceMemberPage, operation_id="workspaces.members.list")
def list_members(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> SpaceMemberPage:
    """List the members of one workspace."""
    members, total = service.list_members(
        session, actor=user, workspace_id=workspace_id, page=PageParams(limit=limit, offset=offset)
    )
    return SpaceMemberPage(members=[_member_view(item) for item in members], total=total)


@router.patch(
    "/{user_id}", response_model=SpaceMemberView, operation_id="workspaces.members.update_role"
)
def update_member_role(
    payload: MemberRoleUpdateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    user_id: UserIdPath,
) -> SpaceMemberView:
    """Change one member's space role; ownership moves only by transfer."""
    with session_transaction(session):
        member = service.update_member_role(
            session, actor=user, workspace_id=workspace_id, user_id=user_id, role=payload.role
        )
    return _member_view(member)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="workspaces.members.remove",
)
def remove_member(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    user_id: UserIdPath,
) -> None:
    """Remove one member, or leave the workspace as that member."""
    with session_transaction(session):
        service.remove_member(session, actor=user, workspace_id=workspace_id, user_id=user_id)


@router.post(
    "/invitations",
    response_model=SpaceInvitationView,
    status_code=status.HTTP_201_CREATED,
    operation_id="workspaces.invitations.create",
)
def create_invitation(
    payload: InvitationCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
) -> SpaceInvitationView:
    """Invite one active user to the workspace under a stated role."""
    with session_transaction(session):
        invitation = service.create_invitation(
            session,
            actor=user,
            workspace_id=workspace_id,
            invitee_user_id=payload.invitee_user_id,
            role=payload.role,
            ttl_seconds=payload.ttl_seconds,
        )
    return _invitation_view(invitation)


@router.get(
    "/invitations",
    response_model=SpaceInvitationPage,
    operation_id="workspaces.invitations.list",
)
def list_invitations(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> SpaceInvitationPage:
    """List the invitations of one workspace."""
    invitations, total = service.list_invitations(
        session, actor=user, workspace_id=workspace_id, page=PageParams(limit=limit, offset=offset)
    )
    return SpaceInvitationPage(
        invitations=[_invitation_view(item) for item in invitations], total=total
    )


@router.delete(
    "/invitations/{invitation_id}",
    response_model=SpaceInvitationView,
    operation_id="workspaces.invitations.revoke",
)
def revoke_invitation(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    invitation_id: InvitationIdPath,
) -> SpaceInvitationView:
    """Revoke one pending invitation; a decided one is never rewritten."""
    with session_transaction(session):
        invitation = service.revoke_invitation(
            session, actor=user, workspace_id=workspace_id, invitation_id=invitation_id
        )
    return _invitation_view(invitation)


@invitation_router.get(
    "/received",
    response_model=SpaceInvitationPage,
    operation_id="workspaces.invitations.received",
)
def list_received_invitations(
    session: DbSession,
    user: CurrentUserDep,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> SpaceInvitationPage:
    """List the workspace invitations addressed to the current user."""
    invitations, total = service.list_received_invitations(
        session, actor=user, page=PageParams(limit=limit, offset=offset)
    )
    return SpaceInvitationPage(
        invitations=[_invitation_view(item) for item in invitations], total=total
    )


@invitation_router.post(
    "/{invitation_id}/accept",
    response_model=SpaceMemberView,
    status_code=status.HTTP_201_CREATED,
    operation_id="workspaces.invitations.accept",
)
def accept_invitation(
    session: DbSession,
    user: CurrentUserDep,
    invitation_id: InvitationIdPath,
) -> SpaceMemberView:
    """Accept one invitation addressed to the current user and join."""
    with session_transaction(session):
        member = service.accept_invitation(session, actor=user, invitation_id=invitation_id)
    return _member_view(member)
