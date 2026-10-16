"""Platform account invitation facts: token digest, validity and state."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

INVITATION_PENDING = "pending"
INVITATION_ACCEPTED = "accepted"
INVITATION_REVOKED = "revoked"
INVITATION_EXPIRED = "expired"
INVITATION_STATUSES = (
    INVITATION_PENDING,
    INVITATION_ACCEPTED,
    INVITATION_REVOKED,
    INVITATION_EXPIRED,
)


class AccountInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One platform account invitation.

    The invitation token is stored only as a SHA-256 digest, so a database
    leak does not yield usable invitations. Expiry is evaluated against the
    stored ``expires_at``; the status column never rewrites history.
    """

    __tablename__ = "account_invitations"

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=INVITATION_PENDING
    )
    invited_by_user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    send_count: Mapped[int] = mapped_column(default=1, nullable=False)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
