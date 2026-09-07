from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Approval(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_workspace_status", "workspace_id", "status"),
        Index("ix_approvals_workspace_run", "workspace_id", "agent_run_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    approval_type: Mapped[str] = mapped_column(String(80), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PendingToolInvocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "pending_tool_invocations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_pending_tool_invocations_workspace_idempotency",
        ),
        UniqueConstraint(
            "approval_id",
            name="uq_pending_tool_invocations_approval",
        ),
        Index(
            "ix_pending_tool_invocations_workspace_status",
            "workspace_id",
            "status",
        ),
        Index(
            "ix_pending_tool_invocations_workspace_run",
            "workspace_id",
            "agent_run_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("approvals.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_arguments: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    policy_decision: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
