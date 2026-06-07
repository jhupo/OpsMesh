from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceScheduledJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_scheduled_jobs"
    __table_args__ = (
        Index("ix_workspace_scheduled_jobs_workspace_status", "workspace_id", "status"),
        Index("ix_workspace_scheduled_jobs_due", "status", "next_run_at"),
        Index("ix_workspace_scheduled_jobs_created_by", "workspace_id", "created_by_user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    schedule_config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    job_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    routing: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    priority: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceScheduledJobEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_scheduled_job_events"
    __table_args__ = (
        Index(
            "ix_workspace_scheduled_job_events_job_created",
            "scheduled_job_id",
            "created_at",
        ),
        Index(
            "ix_workspace_scheduled_job_events_workspace_created",
            "workspace_id",
            "created_at",
        ),
        Index(
            "ix_workspace_scheduled_job_events_workspace_status",
            "workspace_id",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    scheduled_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_scheduled_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    queued_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
