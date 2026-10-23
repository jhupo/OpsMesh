"""Desired schedule state and idempotent delivery receipts."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Trigger(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_triggers"
    __table_args__ = (
        CheckConstraint("kind IN ('schedule', 'webhook')", name="trigger_kind"),
        CheckConstraint("revision > 0 AND applied_revision >= 0", name="trigger_revision"),
    )
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    applied_revision: Mapped[int] = mapped_column(Integer, default=0)
    encrypted_spec: Mapped[str] = mapped_column(Text)
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="active")
    sync_error: Mapped[str | None] = mapped_column(String(100))


class TriggerDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_trigger_deliveries"
    __table_args__ = (UniqueConstraint("trigger_id", "event_id"),)
    trigger_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_triggers.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    target_kind: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[UUID]
