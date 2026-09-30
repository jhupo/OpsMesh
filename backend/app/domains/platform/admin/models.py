from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformMailSettings(TimestampMixin, Base):
    __tablename__ = "platform_mail_settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    password_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    encryption_key_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class PlatformPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_policies"
    __table_args__ = (
        UniqueConstraint("policy_key", name="uq_platform_policies_key"),
        Index("ix_platform_policies_status", "status"),
    )

    policy_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)


class PlatformPolicyEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "platform_policy_events"
    __table_args__ = (
        Index("ix_platform_policy_events_policy", "platform_policy_id"),
        Index("ix_platform_policy_events_created", "created_at"),
    )

    platform_policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_policies.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
