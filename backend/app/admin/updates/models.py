from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformInstallation(TimestampMixin, Base):
    __tablename__ = "platform_installation"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    release_manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)


class PlatformUpdateJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_update_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_platform_update_jobs_idempotency"),
        UniqueConstraint("active_slot", name="uq_platform_update_jobs_active_slot"),
        CheckConstraint("active_slot IS NULL OR active_slot = 1", name="active_slot_valid"),
        CheckConstraint("action IN ('update', 'rollback', 'backup')", name="action_valid"),
        CheckConstraint(
            "status IN ('planning', 'ready', 'queued', 'running', 'succeeded', "
            "'failed', 'recovery_required', 'cancelled')",
            name="status_valid",
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    tag: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planning")
    phase: Mapped[str] = mapped_column(String(40), nullable=False, default="requested")
    plan: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    plan_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    backup_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PlatformUpdateEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "platform_update_events"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("platform_update_jobs.id"), nullable=False)
    phase: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
