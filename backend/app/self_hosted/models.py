from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RuntimeEnrollmentToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_enrollment_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_runtime_enrollment_tokens_hash"),
        Index("ix_runtime_enrollment_tokens_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RuntimeCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_credentials"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_runtime_credentials_hash"),
        Index("ix_runtime_credentials_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SelfHostedWorker(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "self_hosted_workers"
    __table_args__ = (
        UniqueConstraint("workspace_runtime_id", name="uq_self_hosted_workers_runtime"),
        Index("ix_self_hosted_workers_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    machine_id: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    capabilities: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SelfHostedJobClaim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "self_hosted_job_claims"
    __table_args__ = (
        UniqueConstraint("agent_run_id", name="uq_self_hosted_job_claims_run"),
        Index("ix_self_hosted_job_claims_workspace_worker", "workspace_id", "worker_id"),
        Index("ix_self_hosted_job_claims_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_id: Mapped[UUID] = mapped_column(
        ForeignKey("self_hosted_workers.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="claimed")
    claimed_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SelfHostedMcpJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "self_hosted_mcp_jobs"
    __table_args__ = (
        Index("ix_self_hosted_mcp_jobs_workspace_status", "workspace_id", "status"),
        Index("ix_self_hosted_mcp_jobs_workspace_worker", "workspace_id", "worker_id"),
        Index("ix_self_hosted_mcp_jobs_agent_run", "agent_run_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("self_hosted_workers.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    mcp_server_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(160), nullable=False)
    request_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    response_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    claimed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class LocalFileReference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "local_file_references"
    __table_args__ = (
        Index("ix_local_file_references_workspace_runtime", "workspace_id", "workspace_runtime_id"),
        Index("ix_local_file_references_workspace_task", "workspace_id", "task_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    file_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")


class SelfHostedArtifactUpload(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "self_hosted_artifact_uploads"
    __table_args__ = (
        Index("ix_self_hosted_artifact_uploads_workspace_run", "workspace_id", "agent_run_id"),
        Index("ix_self_hosted_artifact_uploads_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_id: Mapped[UUID] = mapped_column(
        ForeignKey("self_hosted_workers.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="registered")
