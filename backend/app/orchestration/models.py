from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrchestrationDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A workspace-owned, versioned user-authored orchestration definition."""

    __tablename__ = "orchestration_definitions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "key",
            name="uq_orchestration_definitions_workspace_key",
        ),
        Index("ix_orchestration_definitions_workspace_status", "workspace_id", "status"),
        Index("ix_orchestration_definitions_workspace_updated", "workspace_id", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchestrationRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable published content; tasks may pin any retained revision."""

    __tablename__ = "orchestration_revisions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_orchestration_revision_version"),
        Index("ix_orchestration_revisions_workspace", "workspace_id", "definition_id"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[UUID] = mapped_column(
        ForeignKey("orchestration_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class SubworkflowInvocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable parent/child boundary for a subworkflow node execution."""

    __tablename__ = "subworkflow_invocations"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="subworkflow_invocation_status_valid",
        ),
        UniqueConstraint("parent_run_id", name="uq_subworkflow_invocations_parent_run"),
        UniqueConstraint("child_task_id", name="uq_subworkflow_invocations_child_task"),
        Index("ix_subworkflow_invocations_workspace_status", "workspace_id", "status"),
        Index(
            "ix_subworkflow_invocations_workspace_parent_task",
            "workspace_id",
            "parent_task_id",
        ),
        Index(
            "ix_subworkflow_invocations_workspace_child_task",
            "workspace_id",
            "child_task_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    parent_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    parent_task_step_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_steps.id", ondelete="CASCADE"), nullable=False
    )
    parent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    child_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[UUID] = mapped_column(
        ForeignKey("orchestration_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    input_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    output_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
