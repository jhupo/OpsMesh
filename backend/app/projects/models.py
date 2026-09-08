from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceProject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_workspace_projects_workspace_slug"),
        CheckConstraint(
            "configuration_version >= 1",
            name="configuration_version_positive",
        ),
        Index("ix_workspace_projects_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    input_path: Mapped[str] = mapped_column(String(512), nullable=False, default="inputs")
    work_path: Mapped[str] = mapped_column(String(512), nullable=False, default="work")
    output_path: Mapped[str] = mapped_column(String(512), nullable=False, default="outputs")
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class WorkspaceProjectConfigurationVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workspace_project_configuration_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint(
            "project_id",
            "version",
            name="uq_workspace_project_configuration_versions_project_version",
        ),
        Index(
            "ix_project_configuration_versions_workspace_project",
            "workspace_id",
            "project_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    change_summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceProjectFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_project_files"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_project_files_active_project_path",
            "project_id",
            "project_path",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "uq_project_files_active_project_file",
            "project_id",
            "workspace_file_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        UniqueConstraint(
            "project_id",
            "project_path",
            "version",
            name="uq_workspace_project_files_project_path_version",
        ),
        Index("ix_project_files_workspace_project", "workspace_id", "project_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_projects.id", ondelete="CASCADE"), nullable=False
    )
    workspace_file_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_files.id", ondelete="RESTRICT"), nullable=False
    )
    supersedes_project_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_project_files.id", ondelete="RESTRICT"), nullable=True
    )
    project_path: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    access_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="read_only")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class WorkspaceProjectOutput(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_project_outputs"
    __table_args__ = (
        Index(
            "uq_project_outputs_active_project_path",
            "project_id",
            "project_path",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_project_outputs_workspace_project", "workspace_id", "project_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_projects.id", ondelete="CASCADE"), nullable=False
    )
    project_path: Mapped[str] = mapped_column(String(512), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(80), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class AgentRunProjectSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_run_project_snapshots"
    __table_args__ = (
        UniqueConstraint("agent_run_id", name="uq_agent_run_project_snapshots_run"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        Index(
            "ix_agent_run_project_snapshots_workspace_project",
            "workspace_id",
            "project_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_projects.id", ondelete="RESTRICT"), nullable=False
    )
    configuration_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_project_configuration_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentRunProjectIOState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_run_project_io_states"
    __table_args__ = (
        UniqueConstraint("agent_run_id", name="uq_agent_run_project_io_states_run"),
        UniqueConstraint("project_snapshot_id", name="uq_agent_run_project_io_states_snapshot"),
        CheckConstraint(
            "status in ('pending', 'staged', 'harvesting', 'harvested', 'failed')",
            name="status_valid",
        ),
        CheckConstraint(
            "staged_file_count >= 0 and staged_bytes >= 0 and "
            "harvested_output_count >= 0 and harvested_bytes >= 0",
            name="counts_non_negative",
        ),
        Index("ix_agent_run_project_io_states_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    project_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_run_project_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="RESTRICT"), nullable=False
    )
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    staged_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    staged_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    harvested_output_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    harvested_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    harvested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
