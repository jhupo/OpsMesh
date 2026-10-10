from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opsmesh.identity.users.models import User
from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from opsmesh.workspaces.members.models import WorkspaceMember


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_workspaces_slug"),
        Index("ix_workspaces_owner_user_id", "owner_user_id"),
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    settings: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    # Private migration rollback data; excluded from all API schemas.
    legacy_approval_settings: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)

    owner: Mapped[User] = relationship()
    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
    )


class WorkspaceHealthSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_health_snapshots"
    __table_args__ = (
        Index("ix_workspace_health_snapshots_workspace_created", "workspace_id", "created_at"),
        Index("ix_workspace_health_snapshots_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    risk_items: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    recommended_actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    trend_basis: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
