from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_workspace_task", "workspace_id", "task_id"),
        Index("ix_agent_runs_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    task_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    runtime_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    input: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RunEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "sequence", name="uq_run_events_run_sequence"),
        Index("ix_run_events_workspace_run", "workspace_id", "agent_run_id"),
        Index("ix_run_events_workspace_type", "workspace_id", "event_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False, default="")
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
