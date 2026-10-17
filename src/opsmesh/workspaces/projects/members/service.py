"""Project membership service: roles, invitations, joining, leaving, removal.

Every operation authorizes through the one project access path. Acceptance
re-reads the invitation under the workspace and project row locks, the
same order the create and revoke paths take, so an invitation whose seat
was revoked while it was pending is refused instead of quietly joining.
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
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.members import access as space_access
from opsmesh.workspaces.projects.members import access
from opsmesh.workspaces.projects.members.models import (
    INVITATION_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REVOKED,
    ProjectInvitation,
    ProjectMember,
)
from opsmesh.workspaces.projects.members.schemas import validated_role


def list_members(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    project_id: UUID,
    page: PageParams,
) -> tuple[list[ProjectMember], int]:
    access.require_project_member(
        session, workspace_id=workspace_id, project_id=project_id, user_id=actor.user_id
    )
    statement = (
        select(ProjectMember)
        .where(
            ProjectMember.workspace_id == workspace_id,
            ProjectMember.project_id == project_id,
        )
        .order_by(ProjectMember.created_at, ProjectMember.user_id)
    )
    return page_scalars(session, statement, page)


def update_member_role(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID,
    role: str,
) -> ProjectMember:
    """Change one member's project role."""
    access.require_project_member(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        permission="manage",
        lock=True,
    )
    member = _member(session, workspace_id=workspace_id, project_id=project_id, user_id=user_id)
    member.role = validated_role(role)
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id, project_id=project_id),
        action="project.member_role_updated",
        target_type="project_member",
        target_id=str(user_id),
        summary="Project member role updated",
        details={"role": member.role},
    )
    return member


def remove_member(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, project_id: UUID, user_id: UUID
) -> None:
    """Remove one project member, or let a member leave the project."""
    self_removal = actor.user_id == user_id
    access.require_project_member(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        permission="read" if self_removal else "manage",
        lock=True,
    )
    member = _member(session, workspace_id=workspace_id, project_id=project_id, user_id=user_id)
    session.delete(member)
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id, project_id=project_id),
        action="project.member_removed",
        target_type="project_member",
        target_id=str(user_id),
        summary="Project member removed",
        details={"self_removal": self_removal},
    )


def create_invitation(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    project_id: UUID,
    invitee_user_id: UUID,
    role: str,
    ttl_seconds: int,
) -> ProjectInvitation:
    """Invite one workspace member who holds no project seat yet."""
    access.require_project_member(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        permission="manage",
        lock=True,
    )
    if space_access.space_role(session, workspace_id=workspace_id, user_id=invitee_user_id) is None:
        raise InvalidArgumentError("The invitee is not a workspace member")
    if session.get(ProjectMember, (project_id, invitee_user_id)) is not None:
        raise ConflictError("The user is already a project member")
    _require_active_user(session, invitee_user_id)
    stated_role = validated_role(role)
    _close_aged_invitations(
        session, workspace_id=workspace_id, project_id=project_id, invitee_id=invitee_user_id
    )
    invitation = ProjectInvitation(
        workspace_id=workspace_id,
        project_id=project_id,
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
        actor=_actor(actor, workspace_id=workspace_id, project_id=project_id),
        action="project.invitation_created",
        target_type="project_invitation",
        target_id=str(invitation.id),
        summary="Project invitation created",
        details={"invitee_id": str(invitee_user_id), "role": stated_role},
    )
    return invitation


def list_invitations(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    project_id: UUID,
    page: PageParams,
) -> tuple[list[ProjectInvitation], int]:
    access.require_project_member(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        permission="manage",
    )
    statement = (
        select(ProjectInvitation)
        .where(
            ProjectInvitation.workspace_id == workspace_id,
            ProjectInvitation.project_id == project_id,
        )
        .order_by(ProjectInvitation.created_at.desc(), ProjectInvitation.id)
    )
    return page_scalars(session, statement, page)


def list_received_invitations(
    session: Session, *, actor: CurrentUser, page: PageParams
) -> tuple[list[ProjectInvitation], int]:
    """The project invitations addressed to the current user."""
    statement = (
        select(ProjectInvitation)
        .where(ProjectInvitation.invitee_id == actor.user_id)
        .order_by(ProjectInvitation.created_at.desc(), ProjectInvitation.id)
    )
    return page_scalars(session, statement, page)


def accept_invitation(
    session: Session, *, actor: CurrentUser, invitation_id: UUID
) -> ProjectMember:
    """Join the project one pending invitation addresses to the current user.

    Lock order is workspace row, project row, invitation row — the same
    order the create and revoke paths take.
    """
    addressed = session.get(ProjectInvitation, invitation_id)
    if addressed is None or addressed.invitee_id != actor.user_id:
        raise NotFoundError("The project invitation does not exist")
    project = access.lock_project(
        session, workspace_id=addressed.workspace_id, project_id=addressed.project_id
    )
    if project.status != "active":
        raise ConflictError("The project is unavailable")
    invitation = session.scalar(
        select(ProjectInvitation)
        .where(ProjectInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invitation is None or invitation.invitee_id != actor.user_id:
        raise NotFoundError("The project invitation does not exist")
    _require_active_user(session, actor.user_id)
    if invitation.effective_state() != INVITATION_PENDING:
        raise ConflictError(
            "The project invitation is no longer pending",
            details={"state": invitation.effective_state()},
        )
    if (
        space_access.space_role(
            session, workspace_id=invitation.workspace_id, user_id=actor.user_id
        )
        is None
    ):
        raise ConflictError("The workspace membership behind this invitation is gone")
    if session.get(ProjectMember, (invitation.project_id, actor.user_id)) is not None:
        raise ConflictError("The user is already a project member")
    member = ProjectMember(
        workspace_id=invitation.workspace_id,
        project_id=invitation.project_id,
        user_id=actor.user_id,
        role=invitation.role,
    )
    session.add(member)
    invitation.state = INVITATION_ACCEPTED
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=invitation.workspace_id, project_id=invitation.project_id),
        action="project.invitation_accepted",
        target_type="project_invitation",
        target_id=str(invitation.id),
        summary="Project invitation accepted",
        details={"role": invitation.role},
    )
    return member


def revoke_invitation(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    project_id: UUID,
    invitation_id: UUID,
) -> ProjectInvitation:
    """Revoke one pending project invitation; a decided one is never rewritten."""
    access.require_project_member(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        permission="manage",
        lock=True,
    )
    invitation = session.scalar(
        select(ProjectInvitation)
        .where(
            ProjectInvitation.id == invitation_id,
            ProjectInvitation.workspace_id == workspace_id,
            ProjectInvitation.project_id == project_id,
        )
        .with_for_update()
    )
    if invitation is None:
        raise NotFoundError("The project invitation does not exist")
    if invitation.effective_state() != INVITATION_PENDING:
        raise ConflictError(
            "Only a pending invitation can be revoked",
            details={"state": invitation.effective_state()},
        )
    invitation.state = INVITATION_REVOKED
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id, project_id=project_id),
        action="project.invitation_revoked",
        target_type="project_invitation",
        target_id=str(invitation.id),
        summary="Project invitation revoked",
        details={},
    )
    return invitation


def _member(
    session: Session, *, workspace_id: UUID, project_id: UUID, user_id: UUID
) -> ProjectMember:
    member = session.get(ProjectMember, (project_id, user_id), populate_existing=True)
    if member is None or member.workspace_id != workspace_id:
        raise NotFoundError("The project member does not exist")
    return member


def _close_aged_invitations(
    session: Session, *, workspace_id: UUID, project_id: UUID, invitee_id: UUID
) -> None:
    """Close pending invitations that aged out so one seat can be re-issued."""
    aged = list(
        session.scalars(
            select(ProjectInvitation).where(
                ProjectInvitation.workspace_id == workspace_id,
                ProjectInvitation.project_id == project_id,
                ProjectInvitation.invitee_id == invitee_id,
                ProjectInvitation.state == INVITATION_PENDING,
                ProjectInvitation.expires_at <= datetime.now(UTC),
            )
        )
    )
    for invitation in aged:
        invitation.state = INVITATION_EXPIRED


def _require_active_user(session: Session, user_id: UUID) -> None:
    profile = session.get(UserProfile, user_id)
    if profile is None or profile.status != USER_STATUS_ACTIVE:
        raise InvalidArgumentError(
            "The user is not an active account", details={"user_id": str(user_id)}
        )


def _actor(actor: CurrentUser, *, workspace_id: UUID, project_id: UUID) -> AuditActor:
    return AuditActor(
        actor_type="user",
        actor_id=str(actor.user_id),
        workspace_id=workspace_id,
        project_id=project_id,
    )


__all__ = [
    "accept_invitation",
    "create_invitation",
    "list_invitations",
    "list_members",
    "list_received_invitations",
    "remove_member",
    "revoke_invitation",
    "update_member_role",
]
