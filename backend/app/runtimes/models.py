from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RuntimeTemplate(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "runtime_templates"
    __table_args__ = (Index("ix_runtime_templates_status", "status"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    image: Mapped[str] = mapped_column(String(260), nullable=False)
    default_limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    default_network_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class WorkspaceRuntime(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_runtimes"
    __table_args__ = (
        Index("ix_workspace_runtimes_workspace_status", "workspace_id", "status"),
        Index(
            "ix_workspace_runtimes_workspace_provider", "workspace_id", "runtime_provider", "status"
        ),
        Index("ix_workspace_runtimes_workspace_runtime_space", "workspace_id", "runtime_space_id"),
        Index("ix_workspace_runtimes_container", "docker_container_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runtime_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    runtime_space_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    runtime_provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="cloud_docker",
    )
    runtime_type: Mapped[str] = mapped_column(String(32), nullable=False, default="docker")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    docker_container_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    network_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    capabilities: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RuntimeEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "runtime_events"
    __table_args__ = (
        Index("ix_runtime_events_workspace_runtime", "workspace_id", "workspace_runtime_id"),
        Index("ix_runtime_events_workspace_runtime_space", "workspace_id", "runtime_space_id"),
        Index("ix_runtime_events_workspace_type", "workspace_id", "event_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_space_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="SET NULL"),
        nullable=True,
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


class RuntimeLease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_leases"
    __table_args__ = (
        UniqueConstraint("workspace_runtime_id", name="uq_runtime_leases_runtime"),
        Index("ix_runtime_leases_workspace_status", "workspace_id", "status"),
        Index("ix_runtime_leases_runtime_space", "workspace_id", "runtime_space_id"),
        Index("ix_runtime_leases_container", "docker_container_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_space_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    docker_container_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    lease_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    acquired_at: Mapped[datetime] = mapped_column(nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RuntimeCommand(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "runtime_commands"
    __table_args__ = (
        Index("ix_runtime_commands_workspace_runtime", "workspace_id", "workspace_runtime_id"),
        Index("ix_runtime_commands_workspace_runtime_space", "workspace_id", "runtime_space_id"),
        Index("ix_runtime_commands_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_space_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runtime_spaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    command: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str] = mapped_column(String, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(String, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
