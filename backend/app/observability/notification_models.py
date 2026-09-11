from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


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
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    notification_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="info")
    source_type: Mapped[str] = mapped_column(String(80), nullable=False, default="system")
    source_id: Mapped[UUID | None] = mapped_column(nullable=True)
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
