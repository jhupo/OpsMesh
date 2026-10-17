"""Space membership service: roles, invitations, joining, leaving, removal.

Every operation authorizes through the one space access path, then decides
under the row locks its state transition needs. Acceptance re-checks the
inviter's current permission and the invitation's own state under the
workspace lock, so an invitation whose inviter lost the manage role while
it was pending is refused instead of quietly joining the workspace.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users.models import USER_STATUS_ACTIVE, UserProfile
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.members import access
from opsmesh.workspaces.members.models import (
    INVITATION_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REVOKED,
    SPACE_ROLE_OWNER,
    SPACE_ROLES,
    WorkspaceInvitation,
    WorkspaceMember,
)
from opsmesh.workspaces.members.schemas import validated_invitation_role


def list_members(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, page: PageParams
) -> tuple[list[WorkspaceMember], int]:
    access.require_workspace_member(session, workspace_id=workspace_id, user_id=actor.user_id)
    statement = (
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at, WorkspaceMember.user_id)
    )
    return page_scalars(session, statement, page)


def update_member_role(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, user_id: UUID, role: str
) -> WorkspaceMember:
    """Change one member's space role; ownership moves only by transfer."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    member = _member(session, workspace_id=workspace_id, user_id=user_id)
    if member.role == SPACE_ROLE_OWNER:
        raise ConflictError("The workspace owner role changes only through a transfer")
    member.role = validated_invitation_role(role)
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="workspace.member_role_updated",
        target_type="workspace_member",
        target_id=str(user_id),
        summary="Workspace member role updated",
        details={"role": member.role},
    )
    return member


def remove_member(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, user_id: UUID
) -> None:
    """Remove one member, or let a member leave; the owner can do neither."""
    self_removal = actor.user_id == user_id
    access.require_workspace_member(
        session,
        workspace_id=workspace_id,
        user_id=actor.user_id,
        permission="read" if self_removal else "manage",
        lock=True,
    )
    member = _member(session, workspace_id=workspace_id, user_id=user_id)
    if member.role == SPACE_ROLE_OWNER:
        raise ConflictError("The workspace owner cannot be removed")
    session.delete(member)
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="workspace.member_removed",
        target_type="workspace_member",
        target_id=str(user_id),
        summary="Workspace member removed",
        details={"self_removal": self_removal},
    )


def create_invitation(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    invitee_user_id: UUID,
    role: str,
    ttl_seconds: int,
) -> WorkspaceInvitation:
    """Invite one active user who is not a member and holds no pending seat."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    if session.get(WorkspaceMember, (workspace_id, invitee_user_id)) is not None:
        raise ConflictError("The user is already a workspace member")
    _require_active_user(session, invitee_user_id)
    stated_role = validated_invitation_role(role)
    _close_aged_invitations(session, workspace_id=workspace_id, invitee_id=invitee_user_id)
    invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        invitee_id=invitee_user_id,
        invited_by_user_id=actor.user_id,
        role=stated_role,
        state=INVITATION_PENDING,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    session.add(invitation)
    session.flush()
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="workspace.invitation_created",
        target_type="workspace_invitation",
        target_id=str(invitation.id),
        summary="Workspace invitation created",
        details={"invitee_id": str(invitee_user_id), "role": stated_role},
    )
    return invitation


def list_invitations(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, page: PageParams
) -> tuple[list[WorkspaceInvitation], int]:
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage"
    )
    statement = (
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id)
        .order_by(WorkspaceInvitation.created_at.desc(), WorkspaceInvitation.id)
    )
    return page_scalars(session, statement, page)


def list_received_invitations(
    session: Session, *, actor: CurrentUser, page: PageParams
) -> tuple[list[WorkspaceInvitation], int]:
    """The invitations addressed to the current user across every workspace."""
    statement = (
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.invitee_id == actor.user_id)
        .order_by(WorkspaceInvitation.created_at.desc(), WorkspaceInvitation.id)
    )
    return page_scalars(session, statement, page)


def accept_invitation(
    session: Session, *, actor: CurrentUser, invitation_id: UUID
) -> WorkspaceMember:
    """Join the workspace one pending invitation addresses to the current user.

    Lock order is workspace row before invitation row, the same order the
    create and revoke paths take: acceptance must not deadlock against a
    revoke that is closing the same invitation.
    """
    addressed = session.get(WorkspaceInvitation, invitation_id)
    if addressed is None or addressed.invitee_id != actor.user_id:
        raise NotFoundError("The workspace invitation does not exist")
    workspace = access.lock_workspace(session, workspace_id=addressed.workspace_id)
    if workspace.status != "active":
        raise ConflictError("The workspace is unavailable")
    invitation = session.scalar(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invitation is None or invitation.invitee_id != actor.user_id:
        raise NotFoundError("The workspace invitation does not exist")
    _require_active_user(session, actor.user_id)
    if invitation.effective_state() != INVITATION_PENDING:
        raise ConflictError(
            "The workspace invitation is no longer pending",
            details={"state": invitation.effective_state()},
        )
    if session.get(WorkspaceMember, (invitation.workspace_id, actor.user_id)) is not None:
        raise ConflictError("The user is already a workspace member")
    inviter = session.get(
        WorkspaceMember, (invitation.workspace_id, invitation.invited_by_user_id)
    )
    if inviter is None or inviter.role not in {"owner", "admin"}:
        raise ForbiddenError("The inviter no longer holds the invitation permission")
    member = WorkspaceMember(
        workspace_id=invitation.workspace_id, user_id=actor.user_id, role=invitation.role
    )
    session.add(member)
    invitation.state = INVITATION_ACCEPTED
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=invitation.workspace_id),
        action="workspace.invitation_accepted",
        target_type="workspace_invitation",
        target_id=str(invitation.id),
        summary="Workspace invitation accepted",
        details={"role": invitation.role},
    )
    return member


def revoke_invitation(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, invitation_id: UUID
) -> WorkspaceInvitation:
    """Revoke one pending invitation; a decided one is never rewritten."""
    access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    invitation = session.scalar(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if invitation is None:
        raise NotFoundError("The workspace invitation does not exist")
    if invitation.effective_state() != INVITATION_PENDING:
        raise ConflictError(
            "Only a pending invitation can be revoked",
            details={"state": invitation.effective_state()},
        )
    invitation.state = INVITATION_REVOKED
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id),
        action="workspace.invitation_revoked",
        target_type="workspace_invitation",
        target_id=str(invitation.id),
        summary="Workspace invitation revoked",
        details={},
    )
    return invitation


def _member(session: Session, *, workspace_id: UUID, user_id: UUID) -> WorkspaceMember:
    member = session.get(WorkspaceMember, (workspace_id, user_id), populate_existing=True)
    if member is None:
        raise NotFoundError("The workspace member does not exist")
    return member


def _close_aged_invitations(
    session: Session, *, workspace_id: UUID, invitee_id: UUID
) -> None:
    """Close pending invitations that aged out so one seat can be re-issued."""
    aged = list(
        session.scalars(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.invitee_id == invitee_id,
                WorkspaceInvitation.state == INVITATION_PENDING,
                WorkspaceInvitation.expires_at <= datetime.now(UTC),
            )
        )
    )
    for invitation in aged:
        invitation.state = INVITATION_EXPIRED


def _require_active_user(session: Session, user_id: UUID) -> None:
    profile = session.get(UserProfile, user_id)
    if profile is None or profile.status != USER_STATUS_ACTIVE:
        raise InvalidArgumentError(
            "The invited user is not an active account", details={"user_id": str(user_id)}
        )


def _actor(actor: CurrentUser, *, workspace_id: UUID) -> AuditActor:
    return AuditActor(
        actor_type="user", actor_id=str(actor.user_id), workspace_id=workspace_id
    )


__all__ = [
    "SPACE_ROLES",
    "accept_invitation",
    "create_invitation",
    "list_invitations",
    "list_members",
    "list_received_invitations",
    "remove_member",
    "revoke_invitation",
    "update_member_role",
]
