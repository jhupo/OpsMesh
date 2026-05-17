from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkerHeartbeat(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        Index("ix_worker_heartbeats_worker", "worker_id"),
        Index("ix_worker_heartbeats_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    worker_id: Mapped[str] = mapped_column(String(160), nullable=False)
    worker_type: Mapped[str] = mapped_column(String(80), nullable=False, default="cloud")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    queue_name: Mapped[str] = mapped_column(String(120), nullable=False, default="agent_runs")
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False)
