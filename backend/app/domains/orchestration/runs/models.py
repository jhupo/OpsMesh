import json
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

AUTHORIZATION_SNAPSHOT_VERSION = 3


def authorization_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "fingerprint"}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{sha256(serialized.encode('utf-8')).hexdigest()}"


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_workspace_task", "workspace_id", "task_id"),
        Index("ix_agent_runs_workspace_status", "workspace_id", "status"),
        Index("ix_agent_runs_workspace_session", "workspace_id", "session_key", "status"),
        Index("ix_agent_runs_workspace_runtime_space", "workspace_id", "runtime_space_id"),
        Index("ix_agent_runs_workspace_execution_runtime", "workspace_id", "execution_runtime_id"),
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
    execution_runtime_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="SET NULL"),
        nullable=True,
    )
    runtime_space_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    input: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    session_key: Mapped[str | None] = mapped_column(String(240), nullable=True)
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
        Index("ix_run_events_workspace_trace", "workspace_id", "trace_id"),
        Index("ix_run_events_workspace_request", "workspace_id", "request_id"),
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
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    runtime_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class AgentRunStateSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_run_state_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            name="uq_agent_run_state_snapshots_run",
        ),
        Index(
            "ix_agent_run_state_snapshots_workspace_status",
            "workspace_id",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    sdk_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    encrypted_state: Mapped[str] = mapped_column(Text, nullable=False)
    state_fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="paused")
