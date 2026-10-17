"""Runtime facts: environments, MCP connections and sandbox operation receipts.

Environments are created, resumed and released explicitly; a run never
spawns one implicitly and a cancelled run never stops an environment it
shares. MCP connections live independently of any run or agent. Every
external effect (sandbox lifecycle, MCP connect/discover/close) is
recorded as a scoped receipt so a retried activity replays instead of
repeating an effect whose outcome is uncertain.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ENVIRONMENT_PENDING = "pending"
ENVIRONMENT_CREATING = "creating"
ENVIRONMENT_READY = "ready"
ENVIRONMENT_RESUMING = "resuming"
ENVIRONMENT_CLOSING = "closing"
ENVIRONMENT_CLOSED = "closed"
ENVIRONMENT_NEEDS_ATTENTION = "needs_attention"
ENVIRONMENT_STATUSES = (
    ENVIRONMENT_PENDING,
    ENVIRONMENT_CREATING,
    ENVIRONMENT_READY,
    ENVIRONMENT_RESUMING,
    ENVIRONMENT_CLOSING,
    ENVIRONMENT_CLOSED,
    ENVIRONMENT_NEEDS_ATTENTION,
)

ENVIRONMENT_ACTION_CREATE = "create"
ENVIRONMENT_ACTION_RESUME = "resume"
ENVIRONMENT_ACTION_CLOSE = "close"
ENVIRONMENT_ACTIONS = (
    ENVIRONMENT_ACTION_CREATE,
    ENVIRONMENT_ACTION_RESUME,
    ENVIRONMENT_ACTION_CLOSE,
)

MCP_TRANSPORT_STREAMABLE_HTTP = "streamable_http"
MCP_TRANSPORT_SSE = "sse"
MCP_TRANSPORT_STDIO = "stdio"
MCP_TRANSPORTS = (MCP_TRANSPORT_STREAMABLE_HTTP, MCP_TRANSPORT_SSE, MCP_TRANSPORT_STDIO)

MCP_PENDING = "pending"
MCP_CONNECTING = "connecting"
MCP_READY = "ready"
MCP_FAILED = "failed"
MCP_CLOSED = "closed"
MCP_CONNECTION_STATUSES = (MCP_PENDING, MCP_CONNECTING, MCP_READY, MCP_FAILED, MCP_CLOSED)

MCP_ACTION_CONNECT = "connect"
MCP_ACTION_DISCOVER = "discover"
MCP_ACTION_CLOSE = "close"
MCP_ACTIONS = (MCP_ACTION_CONNECT, MCP_ACTION_DISCOVER, MCP_ACTION_CLOSE)

RECEIPT_STARTED = "started"
RECEIPT_COMPLETED = "completed"
RECEIPT_UNCERTAIN = "uncertain"
RECEIPT_STATUSES = (RECEIPT_STARTED, RECEIPT_COMPLETED, RECEIPT_UNCERTAIN)

MAX_OWNER_REF_LENGTH = 200
MAX_ERROR_CODE_LENGTH = 100
MAX_KEY_ID_LENGTH = 100
MAX_NAME_LENGTH = 100
MAX_TRANSPORT_LENGTH = 32
MAX_STATUS_LENGTH = 24
MAX_ACTION_LENGTH = 32
MAX_RECEIPT_STATUS_LENGTH = 16
MAX_OPERATION_ID_LENGTH = 240
MAX_FINGERPRINT_LENGTH = 64


class Environment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One explicitly managed execution environment.

    The native session id is the sandbox reference the official client
    resumes from; the encrypted connection state holds whatever the
    sandbox backend needs to continue, which is a real secret and is
    encrypted like every other credential column.
    """

    __tablename__ = "execution_environments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','creating','ready','resuming',"
            "'closing','closed','needs_attention')",
            name="valid_status",
        ),
        Index("ix_execution_environments_scope_owner", "scope_id", "owner_ref"),
    )

    scope_id: Mapped[UUID] = mapped_column(index=True)
    principal_id: Mapped[UUID]
    owner_ref: Mapped[str] = mapped_column(String(MAX_OWNER_REF_LENGTH))
    status: Mapped[str] = mapped_column(String(MAX_STATUS_LENGTH), default=ENVIRONMENT_PENDING)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB)
    native_session_id: Mapped[UUID | None] = mapped_column(unique=True)
    encrypted_state: Mapped[str | None] = mapped_column(Text)
    encryption_key_id: Mapped[str | None] = mapped_column(String(MAX_KEY_ID_LENGTH))
    error_code: Mapped[str | None] = mapped_column(String(MAX_ERROR_CODE_LENGTH))


class MCPConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One MCP connection, independent of any run or agent.

    Remote connections carry an endpoint without credentials (secret
    references are resolved at connect time); a stdio connection runs
    inside one environment, which it references.
    """

    __tablename__ = "execution_mcp_connections"
    __table_args__ = (
        UniqueConstraint("scope_id", "name", name="uq_execution_mcp_connections_scope_name"),
        CheckConstraint(
            "transport IN ('streamable_http', 'sse', 'stdio')",
            name="mcp_connection_transport",
        ),
        CheckConstraint(
            "status IN ('pending','connecting','ready','failed','closed')",
            name="valid_status",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(index=True)
    principal_id: Mapped[UUID]
    owner_ref: Mapped[str] = mapped_column(String(MAX_OWNER_REF_LENGTH))
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH))
    transport: Mapped[str] = mapped_column(String(MAX_TRANSPORT_LENGTH))
    endpoint: Mapped[str | None] = mapped_column(Text)
    environment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_environments.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(MAX_STATUS_LENGTH), default=MCP_PENDING)
    error_code: Mapped[str | None] = mapped_column(String(MAX_ERROR_CODE_LENGTH))


class EnvironmentOperationReceipt(TimestampMixin, Base):
    """One sandbox lifecycle operation of one environment."""

    __tablename__ = "execution_environment_operation_receipts"
    __table_args__ = (
        CheckConstraint(
            "action IN ('create', 'resume', 'close')",
            name="environment_receipt_action",
        ),
        CheckConstraint(
            "status IN ('started', 'completed', 'uncertain')",
            name="valid_status",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(MAX_OPERATION_ID_LENGTH), primary_key=True)
    environment_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_environments.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(MAX_ACTION_LENGTH))
    fingerprint: Mapped[str] = mapped_column(String(MAX_FINGERPRINT_LENGTH))
    status: Mapped[str] = mapped_column(
        String(MAX_RECEIPT_STATUS_LENGTH), default=RECEIPT_STARTED
    )
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    encryption_key_id: Mapped[str | None] = mapped_column(String(MAX_KEY_ID_LENGTH))


class MCPOperationReceipt(TimestampMixin, Base):
    """One MCP connection operation: connect, discovery or close."""

    __tablename__ = "execution_mcp_operation_receipts"
    __table_args__ = (
        CheckConstraint(
            "action IN ('connect', 'discover', 'close')",
            name="mcp_receipt_action",
        ),
        CheckConstraint(
            "status IN ('started', 'completed', 'uncertain')",
            name="valid_status",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(MAX_OPERATION_ID_LENGTH), primary_key=True)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_mcp_connections.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(MAX_ACTION_LENGTH))
    fingerprint: Mapped[str] = mapped_column(String(MAX_FINGERPRINT_LENGTH))
    status: Mapped[str] = mapped_column(
        String(MAX_RECEIPT_STATUS_LENGTH), default=RECEIPT_STARTED
    )
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
