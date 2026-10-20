"""Private Session ownership, native-item staging and atomic operation receipts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

SESSION_OPEN = "open"
SESSION_CLOSING = "closing"
SESSION_CLOSED = "closed"
SESSION_BLOCKED = "blocked"
SESSION_STATUSES = (SESSION_OPEN, SESSION_CLOSING, SESSION_CLOSED, SESSION_BLOCKED)

# The official SessionABC surface; a history operation names its method.
SESSION_METHOD_GET_ITEMS = "get_items"
SESSION_METHOD_ADD_ITEMS = "add_items"
SESSION_METHOD_POP_ITEM = "pop_item"
SESSION_METHOD_CLEAR_SESSION = "clear_session"
SESSION_METHODS = (
    SESSION_METHOD_GET_ITEMS,
    SESSION_METHOD_ADD_ITEMS,
    SESSION_METHOD_POP_ITEM,
    SESSION_METHOD_CLEAR_SESSION,
)

MAX_OWNER_REF_LENGTH = 512
MAX_OPERATION_ID_LENGTH = 512
MAX_METHOD_LENGTH = 32
MAX_RECEIPT_STATUS_LENGTH = 16
MAX_FINGERPRINT_LENGTH = 64

RECEIPT_COMPLETED = "completed"
RECEIPT_STATUSES = (RECEIPT_COMPLETED,)


class SessionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user's native memory; its Task Runs execute in submission order."""

    __tablename__ = "execution_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closing', 'closed', 'blocked')", name="valid_status"),
        CheckConstraint("next_sequence >= 1", name="positive_sequence"),
        Index("ix_execution_sessions_scope_owner", "scope_id", "owner_ref"),
        Index(
            "uq_execution_sessions_configuration_user_open",
            "configuration_id",
            "principal_id",
            unique=True,
            postgresql_where=text("status = 'open' AND configuration_id IS NOT NULL"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(index=True)
    principal_id: Mapped[UUID]
    configuration_id: Mapped[UUID | None] = mapped_column(index=True)
    owner_ref: Mapped[str] = mapped_column(String(MAX_OWNER_REF_LENGTH))
    status: Mapped[str] = mapped_column(
        String(16), default=SESSION_OPEN, server_default=SESSION_OPEN
    )
    next_sequence: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    history_revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class SessionHistoryBatch(UUIDPrimaryKeyMixin, Base):
    """Atomic publication receipt for one Run's staged native items."""

    __tablename__ = "execution_session_history_batches"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_execution_session_history_batch_run"),
        UniqueConstraint("session_id", "committed_revision", name="uq_session_history_revision"),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_sessions.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID | None]
    committed_revision: Mapped[int | None] = mapped_column(BigInteger)


class SessionHistoryItem(UUIDPrimaryKeyMixin, Base):
    """The sole native-item storage; pending rows become committed in place."""

    __tablename__ = "execution_session_history_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "position", name="uq_session_history_item_position"),
        Index("ix_session_history_item_revision", "session_id", "committed_revision"),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_sessions.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_session_history_batches.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(BigInteger)
    committed_revision: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class SessionOperationReceipt(Base):
    """A replay result committed atomically with its native-item mutation."""

    __tablename__ = "execution_session_operation_receipts"
    __table_args__ = (
        CheckConstraint(
            "method IN ('get_items', 'add_items', 'pop_item', 'clear_session')",
            name="session_receipt_method",
        ),
        CheckConstraint(
            "status = 'completed'",
            name="session_receipt_status",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(index=True)
    operation_id: Mapped[str] = mapped_column(String(MAX_OPERATION_ID_LENGTH), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[UUID | None]
    method: Mapped[str] = mapped_column(String(MAX_METHOD_LENGTH))
    status: Mapped[str] = mapped_column(
        String(MAX_RECEIPT_STATUS_LENGTH),
        default=RECEIPT_COMPLETED,
        server_default=RECEIPT_COMPLETED,
    )
    fingerprint: Mapped[str] = mapped_column(String(MAX_FINGERPRINT_LENGTH))
    result: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
