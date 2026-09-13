from __future__ import annotations

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class KnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A workspace-owned source declaration consumed by a later ingestion job."""

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_knowledge_sources_workspace_name",
        ),
        Index("ix_knowledge_sources_workspace_status", "workspace_id", "status"),
        Index("ix_knowledge_sources_workspace_type", "workspace_id", "source_type"),
        CheckConstraint(
            "source_type in ('url', 'workspace_file')",
            name="knowledge_source_type_valid",
        ),
        CheckConstraint(
            "status in ('active', 'paused', 'archived')",
            name="knowledge_source_status_valid",
        ),
        CheckConstraint("version >= 1", name="knowledge_source_version_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    uri: Mapped[str | None] = mapped_column(String(2_048), nullable=True)
    workspace_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_files.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_config: Mapped[dict[str, object]] = mapped_column(
        "config",
        JSONB,
        nullable=False,
        default=dict,
    )
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_ingested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
