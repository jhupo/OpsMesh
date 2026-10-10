from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


@dataclass(frozen=True)
class WorkerRunnerConfig:
    worker_id: str
    worker_type: str = "cloud"
    queue_name: str = "agent_runs"
    concurrency: int = 32
    task_concurrency: int | None = None
    mcp_concurrency: int | None = None
    blocking_io_concurrency: int = 8
    region: str | None = None
    capabilities: tuple[str, ...] = ()
    runtime_modes: tuple[str, ...] = ("isolated", "shared")
    cpu_count: int | None = None
    memory_mb: int | None = None
    heartbeat_interval_seconds: float = 30.0
    idle_sleep_seconds: float = 1.0
    maintenance_interval_seconds: float = 60.0
    run_lease_seconds: int = 900
    recovery_batch_size: int = 100
    job_scan_limit: int = 50
    retry_base_delay_seconds: float = 5.0
    retry_max_delay_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.blocking_io_concurrency < 1:
            raise ValueError("Worker blocking I/O concurrency must be positive")
        if self.concurrency < 1:
            raise ValueError("Worker concurrency must be positive")
        task_concurrency = (
            self.concurrency if self.task_concurrency is None else self.task_concurrency
        )
        mcp_concurrency = (
            self.concurrency if self.mcp_concurrency is None else self.mcp_concurrency
        )
        if task_concurrency < 1:
            raise ValueError("Worker task concurrency must be positive")
        if mcp_concurrency < 1:
            raise ValueError("Worker MCP concurrency must be positive")
        if self.cpu_count is not None and self.cpu_count < 1:
            raise ValueError("Worker CPU capacity must be positive")
        if self.memory_mb is not None and self.memory_mb < 1:
            raise ValueError("Worker memory capacity must be positive")
        if self.region is not None and not self.region.strip():
            raise ValueError("Worker region cannot be blank")
        if any(mode not in {"isolated", "shared"} for mode in self.runtime_modes):
            raise ValueError("Worker runtime modes must be isolated or shared")
        object.__setattr__(self, "task_concurrency", task_concurrency)
        object.__setattr__(self, "mcp_concurrency", mcp_concurrency)

    def capacity(self) -> dict[str, object]:
        capacity: dict[str, object] = {
            "max_jobs": self.concurrency,
            "task_slots": self.task_concurrency,
            "mcp_slots": self.mcp_concurrency,
            "capabilities": list(self.capabilities),
            "runtime_modes": list(self.runtime_modes),
        }
        if self.region is not None:
            capacity["region"] = self.region.strip()
        if self.cpu_count is not None:
            capacity["cpu_count"] = self.cpu_count
        if self.memory_mb is not None:
            capacity["memory_mb"] = self.memory_mb
        return capacity


@dataclass(frozen=True)
class WorkerRunSummary:
    processed: int
    failed: int
    idle_polls: int
    recovered_runs: int
    expired_leases: int
    expired_tool_approvals: int
    stale_runtimes: int
    deleted_runtime_records: int
    lifecycle_backup_jobs_enqueued: int
    lifecycle_backup_jobs_skipped: int
    lifecycle_retention_runs_applied: int
    lifecycle_retention_runs_skipped: int
    lifecycle_restore_drills_completed: int
    lifecycle_restore_drills_skipped: int
    team_execution_loop_jobs_enqueued: int
    team_execution_loop_jobs_skipped: int
    team_execution_loop_skip_reasons: dict[str, int]
    task_events_published: int
    task_event_publish_failures: int
    webhook_delivery_jobs_enqueued: int
    webhook_delivery_jobs_skipped: int
    scheduled_job_actions_enqueued: int
    scheduled_job_actions_recorded: int
    scheduled_job_actions_skipped: int
    scheduled_job_actions_enqueued_by_job_type: dict[str, int]
    scheduled_job_actions_recorded_by_job_type: dict[str, int]
    scheduled_job_actions_skipped_by_job_type: dict[str, int]
    audit_integrity_workspaces_checked: int
    audit_integrity_workspaces_invalid: int
    workspace_health_snapshots_created: int
    workspace_health_snapshots_skipped: int
    workspace_health_snapshots_disabled: int
    queue_rehydrated_runs: int
    queue_recovery_failures: int
    stopped: bool


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
        Index("ix_worker_leases_workspace_trace", "workspace_id", "trace_id"),
        Index("ix_worker_leases_workspace_request", "workspace_id", "request_id"),
        Index("ix_worker_leases_workspace_runtime", "workspace_id", "runtime_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_id: Mapped[str] = mapped_column(String(160), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(120), nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    claim_token: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=lambda: uuid4().hex,
    )
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    runtime_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
