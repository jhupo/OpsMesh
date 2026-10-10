from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceNotification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_notifications"
    __table_args__ = (
        Index("ix_workspace_notifications_workspace_created", "workspace_id", "created_at"),
        Index("ix_workspace_notifications_workspace_read", "workspace_id", "read_at"),
        Index("ix_workspace_notifications_workspace_archived", "workspace_id", "archived_at"),
        Index("ix_workspace_notifications_workspace_severity", "workspace_id", "severity"),
        Index("ix_workspace_notifications_workspace_type", "workspace_id", "notification_type"),
        Index(
            "ix_workspace_notifications_workspace_source",
            "workspace_id",
            "source_type",
            "source_id",
        ),
        Index(
            "ix_workspace_notifications_workspace_recipient_created",
            "workspace_id",
            "recipient_user_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    notification_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="info")
    source_type: Mapped[str] = mapped_column(String(80), nullable=False, default="system")
    source_id: Mapped[UUID | None] = mapped_column(nullable=True)
    recipient_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False, default="")
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformAnnouncement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_announcements"
    __table_args__ = (
        Index("ix_platform_announcements_status_published", "status", "published_at"),
        Index("ix_platform_announcements_created_by", "created_by_user_id"),
    )

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="info")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="published")
    audience: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformAnnouncementRecipient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_announcement_recipients"
    __table_args__ = (
        UniqueConstraint(
            "announcement_id",
            "workspace_id",
            "user_id",
            name="uq_platform_announcement_recipient",
        ),
        Index("ix_platform_announcement_recipients_announcement", "announcement_id"),
        Index(
            "ix_platform_announcement_recipients_workspace_user",
            "workspace_id",
            "user_id",
        ),
    )

    announcement_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_announcements.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    notification_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_notifications.id", ondelete="SET NULL"),
        nullable=True,
    )
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class UserNotificationPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_notification_preferences"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_user_notification_preferences_scope"),
        Index("ix_user_notification_preferences_user", "user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    announcement_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    task_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approval_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    security_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
