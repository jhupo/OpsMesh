from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceMemoryEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_memory_entries"
    __table_args__ = (
        Index("ix_workspace_memory_entries_workspace_status", "workspace_id", "status"),
        Index("ix_workspace_memory_entries_workspace_type", "workspace_id", "entry_type"),
        Index("ix_workspace_memory_entries_workspace_scope", "workspace_id", "visibility_scope"),
        Index("ix_workspace_memory_entries_workspace_source", "workspace_id", "source_type"),
        Index(
            "ix_workspace_memory_entries_workspace_layer_scope",
            "workspace_id",
            "memory_layer",
            "scope_type",
            "scope_id",
            "status",
        ),
        UniqueConstraint(
            "workspace_id",
            "memory_layer",
            "scope_type",
            "scope_id",
            "memory_key",
            name="uq_workspace_memory_layer_scope_key",
        ),
        CheckConstraint(
            "memory_layer IN ('working', 'episodic', 'semantic')",
            name="ck_workspace_memory_layer",
        ),
        CheckConstraint(
            "scope_type IN ('run', 'session', 'task', 'agent', 'team', 'workspace')",
            name="ck_workspace_memory_scope_type",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    memory_layer: Mapped[str] = mapped_column(String(32), nullable=False, default="semantic")
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    scope_id: Mapped[str] = mapped_column(String(240), nullable=False)
    memory_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(80), nullable=False, default="note")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    visibility_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    importance: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorkspaceMemoryVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workspace_memory_versions"
    __table_args__ = (
        UniqueConstraint(
            "memory_entry_id",
            "revision",
            name="uq_workspace_memory_versions_entry_revision",
        ),
        Index(
            "ix_workspace_memory_versions_workspace_entry",
            "workspace_id",
            "memory_entry_id",
            "revision",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_memory_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    changed_by_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    changed_by_agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    change_reason: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
