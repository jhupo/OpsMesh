"""Durable platform observations and workspace-scoped administrator intents."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformMetricSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "platform_metric_snapshots"
    __table_args__ = (Index("ix_platform_metric_snapshots_created", "created_at"),)

    sample_slot: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)


class AdminOperationRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "admin_operation_requests"
    __table_args__ = (Index("ix_admin_operation_requests_status_created", "status", "created_at"),)

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Retain the ID after token deletion so execution can reject a revoked/deleted credential.
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
