"""Run facts: one assembled execution, its events, approvals and receipts.

The run row is the single owner of execution progress: status transitions,
the frozen (encrypted) request and the per-operation receipts that make
model, tool and MCP calls idempotent. Events are the single progress fact
and carry a unique event key so a redelivered append replays instead of
duplicating. Approvals only reference the governance-owned approval
request; the decision itself belongs to governance.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
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

RUN_QUEUED = "queued"
RUN_PREPARING = "preparing"
RUN_RUNNING = "running"
RUN_WAITING_APPROVAL = "waiting_approval"
RUN_CANCELLING = "cancelling"
RUN_FINALIZING = "finalizing"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"
RUN_TIMED_OUT = "timed_out"
RUN_NEEDS_ATTENTION = "needs_attention"
RUN_STATUSES = (
    RUN_QUEUED,
    RUN_PREPARING,
    RUN_RUNNING,
    RUN_WAITING_APPROVAL,
    RUN_CANCELLING,
    RUN_FINALIZING,
    RUN_SUCCEEDED,
    RUN_FAILED,
    RUN_CANCELLED,
    RUN_TIMED_OUT,
    RUN_NEEDS_ATTENTION,
)

RECEIPT_KIND_MODEL = "model"
RECEIPT_KIND_TOOL = "tool"
RECEIPT_KIND_MCP = "mcp"
RECEIPT_KINDS = (RECEIPT_KIND_MODEL, RECEIPT_KIND_TOOL, RECEIPT_KIND_MCP)

MAX_OWNER_REF_LENGTH = 200
MAX_ERROR_CODE_LENGTH = 100
MAX_EVENT_KEY_LENGTH = 240
MAX_EVENT_KIND_LENGTH = 64
MAX_CHECKPOINT_KEY_LENGTH = 240
MAX_RECEIPT_KIND_LENGTH = 16
MAX_RECEIPT_STATUS_LENGTH = 32
MAX_OPERATION_ID_LENGTH = 240
MAX_FINGERPRINT_LENGTH = 64


class Run(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One execution of a frozen request inside one model session.

    The session plus the per-session sequence serialize runs of one
    session: a queued run waits for the running one and never interleaves
    model history with it.
    """

    __tablename__ = "execution_runs"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_execution_runs_session_sequence"),
        CheckConstraint(
            "status IN ('queued','preparing','running','waiting_approval','cancelling',"
            "'finalizing','succeeded','failed','cancelled','timed_out','needs_attention')",
            name="run_status",
        ),
        Index("ix_execution_runs_scope_owner", "scope_id", "owner_ref", "created_at"),
    )

    scope_id: Mapped[UUID] = mapped_column(index=True)
    principal_id: Mapped[UUID]
    owner_ref: Mapped[str] = mapped_column(String(MAX_OWNER_REF_LENGTH))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("execution_sessions.id"))
    sequence: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default=RUN_QUEUED)
    request_ciphertext: Mapped[str] = mapped_column(Text)
    result_ciphertext: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(MAX_ERROR_CODE_LENGTH))
    event_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class RunEvent(UUIDPrimaryKeyMixin, Base):
    """One progress event of one run, keyed for idempotent append."""

    __tablename__ = "execution_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_execution_run_events_run_sequence"),
    )

    scope_id: Mapped[UUID] = mapped_column(index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("execution_runs.id"))
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_key: Mapped[str] = mapped_column(String(MAX_EVENT_KEY_LENGTH), unique=True)
    kind: Mapped[str] = mapped_column(String(MAX_EVENT_KIND_LENGTH))
    payload_ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]


class RunApproval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One SDK approval checkpoint of one run.

    The checkpoint state lets a resumed run continue the native agent; the
    referenced approval request belongs to governance, which alone decides.
    """

    __tablename__ = "execution_run_approvals"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "checkpoint_key", name="uq_execution_run_approvals_run_checkpoint"
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("execution_runs.id"))
    checkpoint_key: Mapped[str] = mapped_column(String(MAX_CHECKPOINT_KEY_LENGTH))
    approval_id: Mapped[UUID | None] = mapped_column(index=True)
    state_ciphertext: Mapped[str] = mapped_column(Text)
    interruptions_ciphertext: Mapped[str] = mapped_column(Text)
    approved: Mapped[bool | None]
    decided_by: Mapped[UUID | None]
    decided_at: Mapped[datetime | None]


class OperationReceipt(TimestampMixin, Base):
    """One external operation of one run, scoped by its stable operation id.

    Model, tool and MCP calls each write a receipt before the call and
    complete it after: a retried activity reads the receipt instead of
    repeating an effect whose outcome is unknown.
    """

    __tablename__ = "execution_operation_receipts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('model', 'tool', 'mcp')",
            name="run_receipt_kind",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(MAX_OPERATION_ID_LENGTH), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("execution_runs.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(MAX_FINGERPRINT_LENGTH))
    kind: Mapped[str] = mapped_column(String(MAX_RECEIPT_KIND_LENGTH))
    accounted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(MAX_RECEIPT_STATUS_LENGTH))
    result_ciphertext: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
