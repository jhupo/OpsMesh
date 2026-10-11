from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


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


class RuntimeHost(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Physical execution capacity, independent of a workspace's execution policies."""

    __tablename__ = "runtime_hosts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_runtime_host_scope"),
        Index(
            "uq_runtime_host_node_key",
            "node_id",
            "workspace_id",
            "host_key",
            unique=True,
            postgresql_where=text("status not in ('deleted','failed')"),
            sqlite_where=text("status not in ('deleted','failed')"),
        ),
        CheckConstraint("capacity between 1 and 128", name="runtime_host_capacity_valid"),
    )

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    host_key: Mapped[str] = mapped_column(String(128), nullable=False)
    image: Mapped[str] = mapped_column(String(260), nullable=False)
    docker_container_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="created", nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    resources: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    provisioning_owner_id: Mapped[UUID | None] = mapped_column(nullable=True)


class WorkspaceRuntime(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_runtimes"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_workspace_runtime_scope"),
        CheckConstraint(
            "execution_mode in ('isolated', 'shared')",
            name="workspace_runtime_execution_mode_valid",
        ),
        Index("ix_workspace_runtimes_workspace_status", "workspace_id", "status"),
        Index(
            "ix_workspace_runtimes_workspace_provider", "workspace_id", "runtime_provider", "status"
        ),
        Index("ix_workspace_runtimes_workspace_runtime_space", "workspace_id", "runtime_space_id"),
        ForeignKeyConstraint(
            ["workspace_id", "host_id"],
            ["runtime_hosts.workspace_id", "runtime_hosts.id"],
        ),
        Index("ix_workspace_runtimes_host", "host_id"),
        Index("ix_workspace_runtimes_workspace_execution_run", "workspace_id", "execution_run_id"),
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
    parent_runtime_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_runtimes.id", ondelete="SET NULL"),
        nullable=True,
    )
    execution_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    runtime_provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="cloud_docker",
    )
    runtime_type: Mapped[str] = mapped_column(String(32), nullable=False, default="docker")
    execution_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="shared",
        server_default="shared",
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    host_id: Mapped[UUID | None] = mapped_column(nullable=True)
    host: Mapped[RuntimeHost | None] = relationship(
        lazy="selectin",
        primaryjoin=lambda: and_(
            WorkspaceRuntime.host_id == RuntimeHost.id,
            WorkspaceRuntime.workspace_id == RuntimeHost.workspace_id,
        ),
        foreign_keys=lambda: [WorkspaceRuntime.host_id],
    )

    @property
    def docker_container_id(self) -> str | None:
        return self.host.docker_container_id if self.host is not None else None

    limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    network_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=lambda: {"mode": "none"}
    )
    capabilities: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RuntimeEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "runtime_events"
    __table_args__ = (
        Index("ix_runtime_events_workspace_runtime", "workspace_id", "workspace_runtime_id"),
        Index("ix_runtime_events_workspace_runtime_space", "workspace_id", "runtime_space_id"),
        Index("ix_runtime_events_workspace_type", "workspace_id", "event_type"),
        Index("ix_runtime_events_workspace_trace", "workspace_id", "trace_id"),
        Index("ix_runtime_events_workspace_request", "workspace_id", "request_id"),
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
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    runtime_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
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


class RuntimeAllocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workspace_runtime_id"],
            ["workspace_runtimes.workspace_id", "workspace_runtimes.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "owner_kind", "owner_id", name="uq_runtime_allocation_owner"
        ),
        CheckConstraint(
            "owner_kind in ('run', 'mcp', 'command')", name="runtime_allocation_kind_valid"
        ),
        Index("ix_runtime_allocations_host", "workspace_id", "workspace_runtime_id"),
        ForeignKeyConstraint(
            ["workspace_id", "host_id"], ["runtime_hosts.workspace_id", "runtime_hosts.id"]
        ),
        UniqueConstraint("host_id", "execution_uid", name="uq_runtime_allocation_identity"),
        CheckConstraint(
            "execution_uid between 100000 and 100127", name="runtime_allocation_uid_valid"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    workspace_runtime_id: Mapped[UUID] = mapped_column(nullable=False)
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(nullable=False)
    host_id: Mapped[UUID] = mapped_column(nullable=False)
    execution_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    network_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


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
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
