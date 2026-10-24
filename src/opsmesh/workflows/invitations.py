"""Explicit grants and authenticated-recipient invitations; no shared Sessions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import current_user, load_identity
from opsmesh.workflows import access
from opsmesh.workflows.contracts import Permission
from opsmesh.workflows.models import WorkflowDefinition, WorkflowGrant, WorkflowInvitation


def _permissions(values: list[Permission]) -> list[str]:
    if not values or not set(values).issubset({"read", "use", "edit"}):
        raise InvalidArgumentError("Declare read, use or edit permissions")
    return sorted(set(values))


def _recipient(session: Session, *, user_id: UUID, owner_id: UUID) -> None:
    if user_id == owner_id:
        raise InvalidArgumentError("The Workflow owner already has all permissions")
    identity = load_identity(session, user_id)
    if identity is None or identity.status != "active":
        raise NotFoundError("The invitation recipient is not an active user")


def list_grants(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    page: PageParams,
) -> tuple[list[WorkflowGrant], int]:
    access.load_workflow(session, actor=actor, workflow_id=workflow_id, owner=True)
    return page_scalars(
        session,
        select(WorkflowGrant)
        .where(WorkflowGrant.workflow_id == workflow_id)
        .order_by(WorkflowGrant.user_id),
        page,
    )


def set_grant(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    user_id: UUID,
    permissions: list[Permission],
) -> WorkflowGrant:
    workflow = access.load_workflow(
        session, actor=actor, workflow_id=workflow_id, owner=True, permission="edit", lock=True
    )
    _recipient(session, user_id=user_id, owner_id=workflow.owner_user_id)
    values = _permissions(permissions)
    grant = session.scalar(
        select(WorkflowGrant).where(
            WorkflowGrant.workflow_id == workflow_id, WorkflowGrant.user_id == user_id
        )
    )
    if grant is None:
        grant = WorkflowGrant(workflow_id=workflow_id, user_id=user_id, permissions=values)
        session.add(grant)
    else:
        grant.permissions = values
    # A pending invitation must not later restore an older, broader permission set.
    session.execute(
        update(WorkflowInvitation)
        .where(
            WorkflowInvitation.workflow_id == workflow_id,
            WorkflowInvitation.recipient_user_id == user_id,
            WorkflowInvitation.status == "pending",
        )
        .values(status="revoked", decided_at=datetime.now(UTC))
    )
    session.flush()
    return grant


def revoke_grant(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    user_id: UUID,
) -> None:
    access.load_workflow(session, actor=actor, workflow_id=workflow_id, owner=True, lock=True)
    grant = session.scalar(
        select(WorkflowGrant).where(
            WorkflowGrant.workflow_id == workflow_id, WorkflowGrant.user_id == user_id
        )
    )
    if grant is not None:
        session.delete(grant)
    session.execute(
        update(WorkflowInvitation)
        .where(
            WorkflowInvitation.workflow_id == workflow_id,
            WorkflowInvitation.recipient_user_id == user_id,
            WorkflowInvitation.status.in_(["pending", "accepted"]),
        )
        .values(status="revoked", decided_at=datetime.now(UTC))
    )
    session.flush()


def create_invitation(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    recipient_user_id: UUID,
    permissions: list[Permission],
    expires_in_hours: int = 168,
) -> WorkflowInvitation:
    workflow = access.load_workflow(
        session, actor=actor, workflow_id=workflow_id, owner=True, permission="edit", lock=True
    )
    _recipient(session, user_id=recipient_user_id, owner_id=workflow.owner_user_id)
    values = _permissions(permissions)
    if not 1 <= expires_in_hours <= 720:
        raise InvalidArgumentError("The invitation lifetime must be between 1 and 720 hours")
    now = datetime.now(UTC)
    existing = session.scalar(
        select(WorkflowInvitation).where(
            WorkflowInvitation.workflow_id == workflow_id,
            WorkflowInvitation.recipient_user_id == recipient_user_id,
            WorkflowInvitation.status == "pending",
        )
    )
    if existing is not None:
        if existing.expires_at > now:
            raise ConflictError("A pending invitation already exists for this user")
        existing.status, existing.decided_at = "revoked", now
        session.flush()
    invitation = WorkflowInvitation(
        workflow_id=workflow_id,
        invited_by_user_id=actor.user_id,
        recipient_user_id=recipient_user_id,
        permissions=values,
        expires_at=now + timedelta(hours=expires_in_hours),
    )
    session.add(invitation)
    session.flush()
    return invitation


def list_invitations(
    session: Session,
    *,
    actor: CurrentUser,
    page: PageParams,
) -> tuple[list[WorkflowInvitation], int]:
    actor = current_user(session, actor.user_id)
    return page_scalars(
        session,
        select(WorkflowInvitation)
        .where(
            or_(
                WorkflowInvitation.recipient_user_id == actor.user_id,
                WorkflowInvitation.invited_by_user_id == actor.user_id,
            )
        )
        .order_by(WorkflowInvitation.created_at.desc(), WorkflowInvitation.id),
        page,
    )


def decide_invitation(
    session: Session,
    *,
    actor: CurrentUser,
    invitation_id: UUID,
    accept: bool,
) -> WorkflowInvitation:
    actor = current_user(session, actor.user_id)
    query = select(WorkflowInvitation).where(
        WorkflowInvitation.id == invitation_id,
        WorkflowInvitation.recipient_user_id == actor.user_id,
    )
    invitation = session.scalar(query)
    if invitation is None:
        raise NotFoundError("The Workflow invitation does not exist")
    # Lock the Workflow before its invitation/grant, as owner mutations do. The
    # recipient need not already have access to accept their first invitation.
    workflow = session.scalar(
        select(WorkflowDefinition)
        .where(WorkflowDefinition.id == invitation.workflow_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    invitation = session.scalar(query.with_for_update().execution_options(populate_existing=True))
    if invitation is None or workflow is None:
        raise NotFoundError("The Workflow invitation does not exist")
    desired = "accepted" if accept else "declined"
    if invitation.status == desired:
        return invitation
    now = datetime.now(UTC)
    if invitation.status != "pending" or invitation.expires_at <= now:
        raise ConflictError("The invitation is no longer pending or has expired")
    if workflow.status != "active":
        raise ConflictError("The Workflow is archived")
    if accept:
        grant = session.scalar(
            select(WorkflowGrant).where(
                WorkflowGrant.workflow_id == workflow.id, WorkflowGrant.user_id == actor.user_id
            )
        )
        if grant is None:
            session.add(
                WorkflowGrant(
                    workflow_id=workflow.id,
                    user_id=actor.user_id,
                    permissions=list(invitation.permissions),
                )
            )
        else:
            grant.permissions = sorted(set(grant.permissions) | set(invitation.permissions))
    invitation.status, invitation.decided_at = desired, now
    session.flush()
    return invitation


def revoke_invitation(
    session: Session,
    *,
    actor: CurrentUser,
    invitation_id: UUID,
) -> None:
    invitation = session.get(WorkflowInvitation, invitation_id, populate_existing=True)
    if invitation is None:
        raise NotFoundError("The Workflow invitation does not exist")
    access.load_workflow(
        session, actor=actor, workflow_id=invitation.workflow_id, owner=True, lock=True
    )
    invitation = session.scalar(
        select(WorkflowInvitation)
        .where(WorkflowInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invitation is None:
        raise NotFoundError("The Workflow invitation does not exist")
    if invitation.status == "accepted":
        raise ConflictError("Revoke the user's grant to remove accepted access")
    if invitation.status == "pending":
        invitation.status, invitation.decided_at = "revoked", datetime.now(UTC)
        session.flush()
