"""Mail facts: the single SMTP configuration and durable send intents."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

SMTP_CONFIGURATION_ID = 1

MAIL_PENDING = "pending"
MAIL_SENDING = "sending"
MAIL_SENT = "sent"
MAIL_FAILED = "failed"
MAIL_CANCELLED = "cancelled"
MAIL_NEEDS_ATTENTION = "needs_attention"
MAIL_STATUSES = (
    MAIL_PENDING,
    MAIL_SENDING,
    MAIL_SENT,
    MAIL_FAILED,
    MAIL_CANCELLED,
    MAIL_NEEDS_ATTENTION,
)
MAIL_SECURITY_MODES = ("tls", "starttls")


class SmtpConfiguration(Base):
    """The single SMTP configuration; its password is stored encrypted."""

    __tablename__ = "smtp_configurations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=SMTP_CONFIGURATION_ID)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    security: Mapped[str] = mapped_column(String(20), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    password_ciphertext: Mapped[str | None] = mapped_column(Text)
    password_key_id: Mapped[str | None] = mapped_column(String(64))
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str] = mapped_column(String(100), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class MailIntent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durable send intent for an explicit, authorized recipient.

    The rendered body is stored encrypted because it may carry an invitation
    token; the subject stays readable for operator status views. A send whose
    outcome cannot be confirmed is never reported as delivered. The source
    revision binds one intent to the version of its source it was rendered
    for, so a rotated source never delivers its stale content.
    """

    __tablename__ = "mail_intents"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_scope", "idempotency_key", name="uq_mail_intents_scope_key"
        ),
    )

    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200))
    source_revision: Mapped[str | None] = mapped_column(String(100))
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    body_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MAIL_PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    reconciled_by_user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciliation_note: Mapped[str | None] = mapped_column(Text)
