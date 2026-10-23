"""Credential and login session facts: expiry and revocation are checked live."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The password verifier of one account; the plaintext never lands here."""

    __tablename__ = "user_credentials"

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)


class LoginSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One login session. The primary key is the random session token.

    Only the SHA-256 digest of the token is stored, so a database leak does
    not yield usable sessions. Expiry and revocation are evaluated on every
    authentication, which makes both take effect immediately.
    """

    __tablename__ = "login_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_login_sessions_token_hash"),
        Index("ix_login_sessions_user_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
