"""Model session facts: ownership, status and history operation receipts.

The session row owns the ownership and the per-session run sequence; the
native message history itself belongs to the official SDK session, whose
tables this module deliberately does not replicate. Every history
operation (model session method) is recorded as an idempotent receipt so
a retried activity replays its result instead of appending twice.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
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
MAX_FINGERPRINT_LENGTH = 64


class SessionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One native model session and its next run sequence.

    Runs of one session serialize on the sequence: submitting a run does
    not occupy an execution slot, it only reserves its place in the
    session order.
    """

    __tablename__ = "execution_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'closing', 'closed', 'blocked')", name="valid_status"
        ),
        CheckConstraint("next_sequence >= 1", name="positive_sequence"),
        Index("ix_execution_sessions_scope_owner", "scope_id", "owner_ref"),
    )

    scope_id: Mapped[UUID] = mapped_column(index=True)
    principal_id: Mapped[UUID]
    owner_ref: Mapped[str] = mapped_column(String(MAX_OWNER_REF_LENGTH))
    status: Mapped[str] = mapped_column(
        String(16), default=SESSION_OPEN, server_default=SESSION_OPEN
    )
    next_sequence: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class SessionOperationReceipt(Base):
    """One idempotent history operation of one session.

    The scope plus the operation id is the identity: two sessions of
    different scopes never share an operation key, and a replayed
    activity reads the recorded result instead of touching the native
    history again.
    """

    __tablename__ = "execution_session_operation_receipts"
    __table_args__ = (
        CheckConstraint(
            "method IN ('get_items', 'add_items', 'pop_item', 'clear_session')",
            name="session_receipt_method",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(MAX_OPERATION_ID_LENGTH), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_sessions.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID | None]
    method: Mapped[str] = mapped_column(String(MAX_METHOD_LENGTH))
    fingerprint: Mapped[str] = mapped_column(String(MAX_FINGERPRINT_LENGTH))
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
