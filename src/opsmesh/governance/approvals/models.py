"""Approval facts: one request per target, one decision, its basis and version."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_CANCELLED = "cancelled"
APPROVAL_EXPIRED = "expired"
APPROVAL_STATUSES = (
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
    APPROVAL_CANCELLED,
    APPROVAL_EXPIRED,
)
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISIONS = (DECISION_APPROVED, DECISION_REJECTED)


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One approval request; the target identity is unique per kind.

    The decision is written once: a second decide on a decided request is a
    conflict, and only a replay of the very same decision idempotency key
    returns the recorded outcome.
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint(
            "kind", "target_type", "target_id", name="uq_approval_requests_target"
        ),
    )

    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=APPROVAL_PENDING)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str] = mapped_column(String(200), nullable=False)
    target_version: Mapped[str | None] = mapped_column(String(100))
    approver_user_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    requester_user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    requester_installation_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    requester_invocation_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decision_idempotency_key: Mapped[str | None] = mapped_column(String(200))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
