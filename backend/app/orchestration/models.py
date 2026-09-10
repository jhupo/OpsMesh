from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrchestrationDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A workspace-owned, versioned user-authored orchestration definition."""

    __tablename__ = "orchestration_definitions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "key",
            name="uq_orchestration_definitions_workspace_key",
        ),
        Index("ix_orchestration_definitions_workspace_status", "workspace_id", "status"),
        Index("ix_orchestration_definitions_workspace_updated", "workspace_id", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
