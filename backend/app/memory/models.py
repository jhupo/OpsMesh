from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
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
    entry_type: Mapped[str] = mapped_column(String(80), nullable=False, default="note")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    visibility_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    importance: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    memory_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(nullable=True)
