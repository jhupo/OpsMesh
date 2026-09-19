"""Durable trigger configuration and inbox; execution remains owned by Task/Run."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Automation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automations"
    __table_args__ = (
        Index("ix_automations_due", "status", "next_due_at"),
        UniqueConstraint("workspace_id", "id", name="uq_automations_workspace_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active")
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB)
    execution_identity: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AutomationEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_install_id"],
            ["plugin_installs.workspace_id", "plugin_installs.id"],
            name="fk_automation_event_plugin_scope",
        ),
        UniqueConstraint("automation_id", "external_event_id", name="uq_automation_event"),
        Index("ix_automation_events_pending", "status", "created_at"),
        Index("ix_automation_events_scan", "status", "checked_at", "created_at"),
        Index(
            "ix_automation_events_conversation", "workspace_id", "automation_id", "conversation_id"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    automation_id: Mapped[UUID] = mapped_column(ForeignKey("automations.id", ondelete="CASCADE"))
    external_event_id: Mapped[str] = mapped_column(String(160))
    source_install_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conversation_id: Mapped[str] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64))
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB)
    input_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    execution_identity: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    reply_delivery_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("webhook_delivery_attempts.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    result_payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    progress_fingerprint: Mapped[str | None] = mapped_column(String(64))
    notification_sequence: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExternalIdentityBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "external_identity_bindings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "automation_id", "sender_id", name="uq_external_identity_sender"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "automation_id"],
            ["automations.workspace_id", "automations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    automation_id: Mapped[UUID] = mapped_column(nullable=False)
    sender_id: Mapped[str] = mapped_column(String(160), nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
