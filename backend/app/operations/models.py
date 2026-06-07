from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
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


class WorkerNode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_nodes"
    __table_args__ = (
        UniqueConstraint("worker_id", name="uq_worker_nodes_worker_id"),
        Index("ix_worker_nodes_status", "status"),
        Index("ix_worker_nodes_type_status", "worker_type", "status"),
    )

    worker_id: Mapped[str] = mapped_column(String(160), nullable=False)
    worker_type: Mapped[str] = mapped_column(String(80), nullable=False, default="cloud")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    queue_name: Mapped[str] = mapped_column(String(120), nullable=False, default="agent_runs")
    worker_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    capacity: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    drain_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False)


class WorkerLease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_leases"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_worker_leases_job_id"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'retrying', 'expired')",
            name="status_valid",
        ),
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        Index("ix_worker_leases_worker_status", "worker_id", "status"),
        Index("ix_worker_leases_workspace_status", "workspace_id", "status"),
        Index("ix_worker_leases_status_heartbeat", "status", "last_heartbeat_at"),
        Index("ix_worker_leases_resource", "workspace_id", "job_type", "resource_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_id: Mapped[str] = mapped_column(String(160), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(120), nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    lease_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
