from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceProject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_workspace_projects_workspace_slug"),
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
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class WorkspaceProjectFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_project_files"
    __table_args__ = (
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
    project_path: Mapped[str] = mapped_column(String(512), nullable=False)
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
