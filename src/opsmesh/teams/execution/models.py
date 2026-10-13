"""Durable Team orchestration requests; Temporal carries only these references."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TeamOrchestrationRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "team_orchestration_requests"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'running', 'completed', 'failed', 'unknown')",
            name="team_orchestration_request_status",
        ),
        Index("ix_team_orchestration_requests_team", "workspace_id", "team_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_teams.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    execution_identity: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
