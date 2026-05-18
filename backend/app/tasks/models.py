from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_workspace_status", "workspace_id", "status"),
        Index("ix_tasks_workspace_team", "workspace_id", "agent_team_id"),
        Index("ix_tasks_workspace_domain", "workspace_id", "domain_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    agent_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    domain_type: Mapped[str] = mapped_column(String(80), nullable=False, default="general")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    generic_state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    domain_state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    team_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    project_plan: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    final_output: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    steps: Mapped[list["TaskStep"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )


class TaskStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_steps"
    __table_args__ = (
        Index("ix_task_steps_workspace_task", "workspace_id", "task_id"),
        Index("ix_task_steps_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    work_package_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    required_role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    required_skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expected_artifacts: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    review_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dependencies: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    result_summary: Mapped[str | None] = mapped_column(String, nullable=True)

    task: Mapped[Task] = relationship(back_populates="steps")


class TaskMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_messages"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_task_messages_task_sequence"),
        Index("ix_task_messages_workspace_task", "workspace_id", "task_id"),
        Index("ix_task_messages_workspace_type", "workspace_id", "message_type"),
        Index("ix_task_messages_workspace_agent", "workspace_id", "agent_profile_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_type: Mapped[str] = mapped_column(String(80), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False, default="")
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
