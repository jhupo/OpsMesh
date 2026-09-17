from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class McpServer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_mcp_servers_workspace_name"),
        Index("ix_mcp_servers_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    server_type: Mapped[str] = mapped_column(String(80), nullable=False, default="stdio")
    connection: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="private")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_health_check_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)


class McpToolAllowlist(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mcp_tool_allowlist"
    __table_args__ = (
        UniqueConstraint("mcp_server_id", "tool_name", name="uq_mcp_tool_allowlist_tool"),
        Index("ix_mcp_tool_allowlist_workspace_server", "workspace_id", "mcp_server_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    mcp_server_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    input_schema: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    capability_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class McpCredentialReference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mcp_credential_references"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_mcp_credential_refs_workspace_name"),
        Index("ix_mcp_credential_refs_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    mcp_server_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    encrypted_secret_payload: Mapped[str | None] = mapped_column(String, nullable=True)
    secret_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    encryption_key_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class McpToolCallLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "mcp_tool_call_logs"
    __table_args__ = (
        Index("ix_mcp_tool_call_logs_workspace_run", "workspace_id", "agent_run_id"),
        Index("ix_mcp_tool_call_logs_workspace_tool", "workspace_id", "tool_name"),
        Index("ix_mcp_tool_call_logs_workspace_status", "workspace_id", "status"),
        Index("ix_mcp_tool_call_logs_workspace_agent", "workspace_id", "agent_profile_id"),
        Index("ix_mcp_tool_call_logs_workspace_trace", "workspace_id", "trace_id"),
        Index("ix_mcp_tool_call_logs_workspace_request", "workspace_id", "request_id"),
        Index("ix_mcp_tool_call_logs_workspace_worker", "workspace_id", "worker_id"),
        Index("ix_mcp_tool_call_logs_workspace_runtime", "workspace_id", "runtime_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    mcp_server_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL"),
        nullable=True,
    )
    tool_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    argument_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    runtime_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    request: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    response: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
