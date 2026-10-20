"""Invitation service: create, resend, revoke and accept platform invitations.

Invitation issuance and consumption share the caller's transaction with audit.
Only token digests are stored; delivery belongs to the invoking client or an
extension. Account opening is injected by bootstrap.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from opsmesh.identity.errors import InvitationStateError
from opsmesh.identity.invitations.contracts import (
    InvitationAccountOpening,
    OpenedInvitationAccount,
)
from opsmesh.identity.invitations.models import (
    INVITATION_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REVOKED,
    AccountInvitation,
)
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.serialization import canonical_json

DEFAULT_INVITATION_TTL_SECONDS = 7 * 24 * 60 * 60
MIN_INVITATION_TTL_SECONDS = 60 * 60
MAX_INVITATION_TTL_SECONDS = 30 * 24 * 60 * 60


def token_digest(token: UUID) -> str:
    """The stored form of an invitation token; the token itself is never stored."""
    return hashlib.sha256(token.bytes).hexdigest()


def create_invitation(
    session: Session,
    *,
    email: str,
    invited_by: AuditActor,
    ttl_seconds: int = DEFAULT_INVITATION_TTL_SECONDS,
) -> tuple[AccountInvitation, UUID]:
    """Create a pending invitation and return its token once for caller delivery."""
    if ttl_seconds < MIN_INVITATION_TTL_SECONDS or ttl_seconds > MAX_INVITATION_TTL_SECONDS:
        raise InvalidArgumentError("The invitation lifetime is out of range")
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"account-invitation:{email}"},
    )
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
        token_revision=1,
        token_issued_at=now,
    )
    session.add(invitation)
    session.flush()
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="invitation.created",
            actor=invited_by,
            source="platform",
            target_type="account_invitation",
            target_id=str(invitation.id),
            summary=f"Account invitation created for {email}",
            payload_json=canonical_json({"expires_at": invitation.expires_at.isoformat()}),
        ),
    )
    return invitation, token


def resend_invitation(
    session: Session,
    *,
    invitation_id: UUID,
    invited_by: AuditActor,
) -> tuple[AccountInvitation, UUID]:
    """Issue a replacement token for a pending invitation.

    The stored form is a one-way digest, so a resend rotates the code: the
    previous token stops working when the replacement commits. A closed or
    expired invitation cannot be reissued. Acceptance and reissuance lock the
    same invitation row to serialize these transitions.
    """
    invitation = _locked_invitation(session, invitation_id)
    if invitation.status != INVITATION_PENDING:
        raise InvitationStateError(details={"status": invitation.status})
    if invitation.expires_at <= datetime.now(UTC):
        raise InvitationStateError(
            "The invitation has expired", details={"status": INVITATION_EXPIRED}
        )
    token = uuid4()
    invitation.token_hash = token_digest(token)
    invitation.token_revision += 1
    invitation.token_issued_at = datetime.now(UTC)
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="invitation.resent",
            actor=invited_by,
            source="platform",
            target_type="account_invitation",
            target_id=str(invitation.id),
            summary=f"Account invitation token reissued for {invitation.email}",
            payload_json=canonical_json(
                {"token_revision": invitation.token_revision, "token_rotated": True}
            ),
        ),
    )
    return invitation, token


def revoke_invitation(
    session: Session, *, invitation_id: UUID, actor: AuditActor
) -> AccountInvitation:
    """Revoke one pending invitation; its token stops working immediately.

    The row is locked for the whole transition: a revoke that read a stale
    pending snapshot could otherwise overwrite an acceptance that committed
    while it waited, reporting a revocation of a consumed invitation.
    """
    invitation = _locked_invitation(session, invitation_id)
    if invitation.status != INVITATION_PENDING:
        raise InvitationStateError(details={"status": invitation.status})
    invitation.status = INVITATION_REVOKED
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="invitation.revoked",
            actor=actor,
            source="platform",
            target_type="account_invitation",
            target_id=str(invitation.id),
            summary=f"Account invitation revoked for {invitation.email}",
            payload_json=canonical_json({}),
        ),
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
        .execution_options(populate_existing=True)
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
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="invitation.accepted",
            actor=AuditActor(actor_type="user", actor_id=str(opened.user_id)),
            source="platform",
            target_type="account_invitation",
            target_id=str(invitation.id),
            summary=f"Account invitation accepted for {invitation.email}",
            payload_json=canonical_json({"user_id": str(opened.user_id)}),
        ),
    )
    return opened


def get_invitation(session: Session, invitation_id: UUID) -> AccountInvitation:
    invitation = session.get(AccountInvitation, invitation_id)
    if invitation is None:
        raise NotFoundError("The invitation does not exist")
    return invitation


def _locked_invitation(session: Session, invitation_id: UUID) -> AccountInvitation:
    """Read one invitation for a state transition under its row lock.

    Acceptance, revocation and rotation all decide under this lock: a plain
    read would let a later transition overwrite a state that committed while
    the earlier transaction was still working from its own snapshot.
    """
    invitation = session.scalar(
        select(AccountInvitation)
        .where(AccountInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
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
        now = datetime.now(UTC)
        if status == INVITATION_EXPIRED:
            # Expiry is computed, not stored: pending rows past their validity.
            query = query.where(
                AccountInvitation.status == INVITATION_PENDING,
                AccountInvitation.expires_at <= now,
            )
            count_query = count_query.where(
                AccountInvitation.status == INVITATION_PENDING,
                AccountInvitation.expires_at <= now,
            )
        elif status == INVITATION_PENDING:
            query = query.where(
                AccountInvitation.status == INVITATION_PENDING,
                AccountInvitation.expires_at > now,
            )
            count_query = count_query.where(
                AccountInvitation.status == INVITATION_PENDING,
                AccountInvitation.expires_at > now,
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
