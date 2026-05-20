from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, UUIDPrimaryKeyMixin


class TaskPlanningAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "task_planning_attempts"
    __table_args__ = (
        Index("ix_task_planning_attempts_workspace_task", "workspace_id", "task_id"),
        Index("ix_task_planning_attempts_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    planner_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    output_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    validation_errors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
