"""Security event facts: login anomalies, access denials and rejections."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MAX_KIND_LENGTH = 100
MAX_SEVERITY_LENGTH = 20
MAX_SUMMARY_LENGTH = 500
SEVERITIES = ("info", "warning", "critical")


class SecurityEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One recorded security fact; it is never copied into the audit chain."""

    __tablename__ = "security_events"

    kind: Mapped[str] = mapped_column(String(MAX_KIND_LENGTH), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(
        String(MAX_SEVERITY_LENGTH), nullable=False, default="warning"
    )
    summary: Mapped[str] = mapped_column(String(MAX_SUMMARY_LENGTH), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    user_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True), index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
