from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceQuota(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_quotas"
    __table_args__ = (
        UniqueConstraint("workspace_id", "quota_key", name="uq_workspace_quotas_key"),
        CheckConstraint("limit_value >= 0", name="limit_value_non_negative"),
        CheckConstraint("reserved_value >= 0", name="reserved_value_non_negative"),
        Index("ix_workspace_quotas_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    quota_key: Mapped[str] = mapped_column(String(80), nullable=False)
    limit_value: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="count")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class WorkspaceReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_reservations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "reservation_key", name="uq_workspace_reservations_key"),
        Index("ix_workspace_reservations_workspace_status", "workspace_id", "status"),
        Index(
            "ix_workspace_reservations_workspace_project_status",
            "workspace_id",
            "workspace_project_id",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_projects.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    task_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_steps.id", ondelete="SET NULL"),
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    reservation_key: Mapped[str] = mapped_column(String(180), nullable=False)
    resource_usage: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)
