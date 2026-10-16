"""Invitation service: create, resend, revoke and accept platform invitations.

The invitation fact, its state transitions and the rendered mail content are
owned here; the account opening and the mail registration are injected
contracts assembled by bootstrap. Every transition is audited inside the
caller's transaction.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.errors import InvitationStateError
from opsmesh.identity.invitations.contracts import (
    InvitationAccountOpening,
    InvitationMailSender,
    OpenedInvitationAccount,
)
from opsmesh.identity.invitations.models import (
    INVITATION_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REVOKED,
    AccountInvitation,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams

DEFAULT_INVITATION_TTL_SECONDS = 7 * 24 * 60 * 60
MIN_INVITATION_TTL_SECONDS = 60 * 60
MAX_INVITATION_TTL_SECONDS = 30 * 24 * 60 * 60
INVITATION_MAIL_SUBJECT = "You are invited to OpsMesh"


def token_digest(token: UUID) -> str:
    """The stored form of an invitation token; the token itself is never stored."""
    return hashlib.sha256(token.bytes).hexdigest()


def create_invitation(
    session: Session,
    *,
    email: str,
    invited_by: AuditActor,
    sender: InvitationMailSender,
    ttl_seconds: int = DEFAULT_INVITATION_TTL_SECONDS,
    platform_base_url: str | None = None,
) -> tuple[AccountInvitation, UUID]:
    """Create one pending invitation and register its mail in the same transaction."""
    if ttl_seconds < MIN_INVITATION_TTL_SECONDS or ttl_seconds > MAX_INVITATION_TTL_SECONDS:
        raise InvalidArgumentError("The invitation lifetime is out of range")
    if session.scalar(
        select(AccountInvitation).where(
            AccountInvitation.email == email,
            AccountInvitation.status == INVITATION_PENDING,
            AccountInvitation.expires_at > datetime.now(UTC),
        )
    ):
        raise ConflictError(
            "A pending invitation already exists for this address",
            details={"email_domain": email.rsplit("@", 1)[-1]},
        )
    token = uuid4()
    now = datetime.now(UTC)
    invitation = AccountInvitation(
        email=email,
        token_hash=token_digest(token),
        status=INVITATION_PENDING,
        invited_by_user_id=UUID(invited_by.actor_id) if invited_by.actor_id else None,
        expires_at=now + timedelta(seconds=ttl_seconds),
        send_count=1,
        last_sent_at=now,
    )
    session.add(invitation)
    session.flush()
    sender.send_account_invitation(
        session,
        invitation_id=invitation.id,
        recipient=email,
        subject=INVITATION_MAIL_SUBJECT,
        body=render_invitation_body(
            token=token,
            expires_at=invitation.expires_at,
            base_url=platform_base_url,
        ),
        idempotency_key=f"invitation-mail:{invitation.id}:1",
    )
    append_platform_event(
        session,
        actor=invited_by,
        action="invitation.created",
        target_type="account_invitation",
        target_id=str(invitation.id),
        summary=f"Account invitation created for {email}",
        details={"expires_at": invitation.expires_at.isoformat()},
    )
    return invitation, token


def resend_invitation(
    session: Session,
    *,
    invitation_id: UUID,
    invited_by: AuditActor,
    sender: InvitationMailSender,
    platform_base_url: str | None = None,
) -> AccountInvitation:
    """Send the invitation again with a freshly issued token.

    The stored form is a one-way digest, so a resend rotates the code: the
    previously delivered token stops working the moment the new one is sent.
    A closed or expired invitation is never resent.
    """
    invitation = session.get(AccountInvitation, invitation_id)
    if invitation is None:
        raise NotFoundError("The invitation does not exist")
    if invitation.status != INVITATION_PENDING:
        raise InvitationStateError(details={"status": invitation.status})
    if invitation.expires_at <= datetime.now(UTC):
        raise InvitationStateError(
            "The invitation has expired", details={"status": INVITATION_EXPIRED}
        )
    token = uuid4()
    invitation.token_hash = token_digest(token)
    invitation.send_count += 1
    invitation.last_sent_at = datetime.now(UTC)
    sender.send_account_invitation(
        session,
        invitation_id=invitation.id,
        recipient=invitation.email,
        subject=INVITATION_MAIL_SUBJECT,
        body=render_invitation_body(
            token=token,
            expires_at=invitation.expires_at,
            base_url=platform_base_url,
        ),
        idempotency_key=f"invitation-mail:{invitation.id}:{invitation.send_count}",
    )
    append_platform_event(
        session,
        actor=invited_by,
        action="invitation.resent",
        target_type="account_invitation",
        target_id=str(invitation.id),
        summary=f"Account invitation resent to {invitation.email}",
        details={"send_count": invitation.send_count, "token_rotated": True},
    )
    return invitation


def revoke_invitation(
    session: Session, *, invitation_id: UUID, actor: AuditActor
) -> AccountInvitation:
    """Revoke one pending invitation; its token stops working immediately."""
    invitation = session.get(AccountInvitation, invitation_id)
    if invitation is None:
        raise NotFoundError("The invitation does not exist")
    if invitation.status != INVITATION_PENDING:
        raise InvitationStateError(details={"status": invitation.status})
    invitation.status = INVITATION_REVOKED
    append_platform_event(
        session,
        actor=actor,
        action="invitation.revoked",
        target_type="account_invitation",
        target_id=str(invitation.id),
        summary=f"Account invitation revoked for {invitation.email}",
        details={},
    )
    return invitation


def accept_invitation(
    session: Session,
    *,
    token: UUID,
    display_name: str,
    password: str,
    account_opening: InvitationAccountOpening,
) -> OpenedInvitationAccount:
    """Accept one invitation: open the account and consume the invitation."""
    invitation = session.scalar(
        select(AccountInvitation)
        .where(AccountInvitation.token_hash == token_digest(token))
        .with_for_update()
    )
    if invitation is None:
        raise NotFoundError("The invitation does not exist")
    if invitation.status != INVITATION_PENDING:
        raise InvitationStateError(details={"status": invitation.status})
    if invitation.expires_at <= datetime.now(UTC):
        raise InvitationStateError(
            "The invitation has expired", details={"status": INVITATION_EXPIRED}
        )
    opened = account_opening.open_account(
        session, email=invitation.email, display_name=display_name, password=password
    )
    invitation.status = INVITATION_ACCEPTED
    invitation.accepted_at = datetime.now(UTC)
    invitation.accepted_user_id = opened.user_id
    append_platform_event(
        session,
        actor=AuditActor(actor_type="user", actor_id=str(opened.user_id)),
        action="invitation.accepted",
        target_type="account_invitation",
        target_id=str(invitation.id),
        summary=f"Account invitation accepted for {invitation.email}",
        details={"user_id": str(opened.user_id)},
    )
    return opened


def get_invitation(session: Session, invitation_id: UUID) -> AccountInvitation:
    invitation = session.get(AccountInvitation, invitation_id)
    if invitation is None:
        raise NotFoundError("The invitation does not exist")
    return invitation


def list_invitations(
    session: Session,
    *,
    page: PageParams,
    status: str | None = None,
) -> tuple[list[AccountInvitation], int]:
    """One offset page of invitations plus the matching total count."""
    query = select(AccountInvitation)
    count_query = select(func.count()).select_from(AccountInvitation)
    if status is not None:
        if status == INVITATION_EXPIRED:
            # Expiry is computed, not stored: pending rows past their validity.
            query = query.where(
                AccountInvitation.status == INVITATION_PENDING,
                AccountInvitation.expires_at <= datetime.now(UTC),
            )
            count_query = count_query.where(
                AccountInvitation.status == INVITATION_PENDING,
                AccountInvitation.expires_at <= datetime.now(UTC),
            )
        else:
            query = query.where(AccountInvitation.status == status)
            count_query = count_query.where(AccountInvitation.status == status)
    total = session.scalar(count_query) or 0
    rows = list(
        session.scalars(
            query.order_by(AccountInvitation.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    return rows, total


def effective_status(invitation: AccountInvitation, *, now: datetime | None = None) -> str:
    """The state one invitation presents: expiry is derived, never rewritten."""
    if invitation.status != INVITATION_PENDING:
        return invitation.status
    if invitation.expires_at <= (now or datetime.now(UTC)):
        return INVITATION_EXPIRED
    return INVITATION_PENDING


def render_invitation_body(*, token: UUID, expires_at: datetime, base_url: str | None) -> str:
    """Render the invitation mail body owned by identity, not by the mail module."""
    lines = [
        "You have been invited to open an OpsMesh account.",
        f"Invitation code: {token}",
        f"This invitation expires at {expires_at.isoformat()}.",
    ]
    if base_url:
        lines.append(f"Accept it at {base_url.rstrip('/')}/accept-invitation?token={token}")
    return "\n".join(lines)
