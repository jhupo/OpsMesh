"""Workspace notification facts: inbox rows, per-user receipts and preferences.

A notification is one fact addressed to one member; read and archive are
per-user receipts on that same row, not a second copy of it. Creation is
deduplicated by its source, so a producer that retries the same source
reference lands one inbox row. Delivery strategy — who is notified, how,
and in which batches — belongs to the notification extension and the flow
engine, not to this table.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

NOTIFICATION_SEVERITY_INFO = "info"
NOTIFICATION_SEVERITY_WARNING = "warning"
NOTIFICATION_SEVERITY_CRITICAL = "critical"
NOTIFICATION_SEVERITIES = (
    NOTIFICATION_SEVERITY_INFO,
    NOTIFICATION_SEVERITY_WARNING,
    NOTIFICATION_SEVERITY_CRITICAL,
)

MAX_TYPE_LENGTH = 80
MAX_TITLE_LENGTH = 240
MAX_BODY_LENGTH = 8000


class WorkspaceNotification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One notification addressed to one workspace member."""

    __tablename__ = "workspace_notifications"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name="workspace_notification_severity"
        ),
        UniqueConstraint(
            "workspace_id",
            "recipient_user_id",
            "source_type",
            "source_id",
            "notification_type",
            name="uq_workspace_notification_source_recipient",
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_workspace_notifications_recipient_created",
            "workspace_id",
            "recipient_user_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    notification_type: Mapped[str] = mapped_column(String(MAX_TYPE_LENGTH), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(32), nullable=False, default=NOTIFICATION_SEVERITY_INFO
    )
    source_type: Mapped[str] = mapped_column(String(MAX_TYPE_LENGTH), nullable=False)
    source_id: Mapped[UUID | None] = mapped_column(default=None)
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    read_at: Mapped[datetime | None] = mapped_column(default=None)
    archived_at: Mapped[datetime | None] = mapped_column(default=None)


class NotificationPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One member's per-workspace notification preferences."""

    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", name="uq_notification_preferences_scope"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
