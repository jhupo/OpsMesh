"""Append-only audit facts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, UUIDPrimaryKeyMixin

MAX_SUMMARY_LENGTH = 2000
MAX_ACTION_LENGTH = 200
MAX_TARGET_LENGTH = 200


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """One appended audit fact; rows are never updated or deleted."""

    __tablename__ = "audit_events"

    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    operation_id: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[str | None] = mapped_column(String(255))
    causation_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(MAX_ACTION_LENGTH), nullable=False)
    target_type: Mapped[str] = mapped_column(String(MAX_TARGET_LENGTH), nullable=False)
    target_id: Mapped[str] = mapped_column(String(MAX_TARGET_LENGTH), nullable=False)
    summary: Mapped[str] = mapped_column(String(MAX_SUMMARY_LENGTH), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
