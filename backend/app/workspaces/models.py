from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.app.identity.models import User


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

    owner: Mapped["User"] = relationship()
    members: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
    )


class WorkspaceMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_workspace_user"),
        Index("ix_workspace_members_user_id", "user_id"),
        Index("ix_workspace_members_workspace_role", "workspace_id", "role"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)

    workspace: Mapped[Workspace] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="workspace_memberships")


class WorkspaceQuota(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_quotas"
    __table_args__ = (
        UniqueConstraint("workspace_id", "quota_key", name="uq_workspace_quotas_key"),
        Index("ix_workspace_quotas_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    quota_key: Mapped[str] = mapped_column(String(80), nullable=False)
    limit_value: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="count")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class WorkspaceReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_reservations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "reservation_key", name="uq_workspace_reservations_key"),
        Index("ix_workspace_reservations_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    task_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_steps.id", ondelete="SET NULL"),
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    reservation_key: Mapped[str] = mapped_column(String(180), nullable=False)
    resource_usage: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)
