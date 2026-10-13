from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceExportJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILED = "reconciled"


class WorkspaceExportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_export_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'unknown', 'reconciled')",
            name="export_job_status",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_workspace_export_jobs_scope"),
        Index("ix_workspace_export_jobs_workspace_status", "workspace_id", "status"),
        Index("ix_workspace_export_jobs_created_by", "workspace_id", "created_by_user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    export_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    request: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(260), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    job_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    source_release: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_schema_revision: Mapped[str | None] = mapped_column(String(120), nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_identity: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class WorkspaceRestoreDrillOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_restore_drill_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "export_job_id"],
            ["workspace_export_jobs.workspace_id", "workspace_export_jobs.id"],
            name="fk_restore_drill_export_scope",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'unknown', 'reconciled')",
            name="restore_drill_status",
        ),
        Index("ix_restore_drill_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    export_job_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    temporary_workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    request: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    execution_identity: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    origin: Mapped[str] = mapped_column(String(40), nullable=False, default="user")


class WorkspaceArchiveVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_archive_verifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "export_job_id"],
            ["workspace_export_jobs.workspace_id", "workspace_export_jobs.id"],
            name="fk_archive_verification_export_scope",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="archive_verification_status",
        ),
        Index("ix_archive_verifications_workspace_status", "workspace_id", "status"),
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    export_job_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(100))
    execution_identity: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    origin: Mapped[str] = mapped_column(String(40), nullable=False, default="user")
