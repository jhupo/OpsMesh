from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RuntimeSpace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_spaces"
    __table_args__ = (
        Index("ix_runtime_spaces_workspace_status", "workspace_id", "status"),
        Index("ix_runtime_spaces_workspace_scope", "workspace_id", "scope"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_runtime_template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runtime_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    network_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    storage_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    cleanup_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )


class RuntimeSpaceBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_space_bindings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "target_type",
            "target_id",
            name="uq_runtime_space_bindings_target",
        ),
        Index("ix_runtime_space_bindings_space", "workspace_id", "runtime_space_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_space_id: Mapped[UUID] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class RuntimeSpaceQuota(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_space_quotas"
    __table_args__ = (
        UniqueConstraint(
            "runtime_space_id",
            "quota_key",
            name="uq_runtime_space_quotas_space_key",
        ),
        CheckConstraint("limit_value >= 0", name="limit_value_non_negative"),
        CheckConstraint("reserved_value >= 0", name="reserved_value_non_negative"),
        Index("ix_runtime_space_quotas_workspace_space", "workspace_id", "runtime_space_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_space_id: Mapped[UUID] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    quota_key: Mapped[str] = mapped_column(String(80), nullable=False)
    limit_value: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="count")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class RuntimeSpaceReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_space_reservations"
    __table_args__ = (
        UniqueConstraint(
            "runtime_space_id",
            "reservation_key",
            name="uq_runtime_space_reservations_space_key",
        ),
        Index("ix_runtime_space_reservations_workspace_status", "workspace_id", "status"),
        Index("ix_runtime_space_reservations_space_status", "runtime_space_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_space_id: Mapped[UUID] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="CASCADE"),
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
    resource_usage: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RuntimeSpaceEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "runtime_space_events"
    __table_args__ = (
        Index("ix_runtime_space_events_space", "workspace_id", "runtime_space_id"),
        Index("ix_runtime_space_events_workspace_type", "workspace_id", "event_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_space_id: Mapped[UUID] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False, default="")
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
