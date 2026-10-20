"""Composition-owned state and explicit execution operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FlowRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flow_runs"

    revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("orchestration_definition_revisions.id")
    )
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    configuration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("orchestration_configurations.id"), index=True
    )
    initiated_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    root_id: Mapped[UUID] = mapped_column(ForeignKey("flow_runs.id"), index=True)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("flow_runs.id"), index=True)
    parent_invocation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("extension_invocations.id")
    )
    depth: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    command_sequence: Mapped[int] = mapped_column(Integer, default=0)
    workflow_generation: Mapped[int] = mapped_column(Integer, default=1)
    control_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    deadline: Mapped[datetime] = mapped_column()
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    graph_ciphertext: Mapped[str] = mapped_column()
    input_ciphertext: Mapped[str] = mapped_column()
    output_ciphertext: Mapped[str | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_steps: Mapped[int] = mapped_column(Integer, default=0)
    active_steps: Mapped[int] = mapped_column(Integer, default=0)
    allocated_tokens: Mapped[int] = mapped_column(Integer, default=0)


class StepAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flow_step_attempts"
    __table_args__ = (UniqueConstraint("run_id", "path", "generation"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("flow_runs.id"), index=True)
    path: Mapped[str] = mapped_column(String(500))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="started")
    node_ciphertext: Mapped[str] = mapped_column()
    input_ciphertext: Mapped[str] = mapped_column()
    result_ciphertext: Mapped[str | None] = mapped_column(nullable=True)
    child_kind: Mapped[str | None] = mapped_column(String(24))
    child_id: Mapped[UUID | None] = mapped_column(index=True)
    wait_deadline: Mapped[datetime | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100))


class FlowCommand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flow_commands"
    __table_args__ = (
        UniqueConstraint("run_id", "command_id", name="uq_flow_commands_identity"),
        UniqueConstraint("run_id", "sequence", name="uq_flow_commands_sequence"),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("flow_runs.id"), index=True)
    command_id: Mapped[UUID] = mapped_column()
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload_ciphertext: Mapped[str] = mapped_column()


class FlowSignalReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flow_signal_receipts"
    __table_args__ = (UniqueConstraint("run_id", "step_path", "generation", "name"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("flow_runs.id"))
    step_path: Mapped[str] = mapped_column(String(500))
    generation: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    command_id: Mapped[UUID] = mapped_column()
    value_ciphertext: Mapped[str] = mapped_column()
    consumed: Mapped[bool] = mapped_column(default=False)


class FlowPlanApplication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flow_plan_applications"
    __table_args__ = (UniqueConstraint("run_id", "step_path"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("flow_runs.id"))
    step_path: Mapped[str] = mapped_column(String(500))
    source_invocation_id: Mapped[UUID] = mapped_column(ForeignKey("extension_invocations.id"))
    revision: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    graph_ciphertext: Mapped[str] = mapped_column()


class FlowProjectionReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flow_projection_receipts"
    __table_args__ = (UniqueConstraint("run_id", "projection_id"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("flow_runs.id"))
    projection_id: Mapped[str] = mapped_column(String(240))
    fingerprint: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)


class FlowTokenGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Conservative per-operation admission against one root flow's Token bound."""

    __tablename__ = "flow_token_grants"
    __table_args__ = (UniqueConstraint("root_id", "operation_id"),)

    root_id: Mapped[UUID] = mapped_column(ForeignKey("flow_runs.id"))
    operation_id: Mapped[str] = mapped_column(String(240))
    tokens: Mapped[int] = mapped_column(Integer)


class FlowEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flow_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("flow_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


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

# A run is active while it can still write its session's history: queued,
# preparing, running, waiting for an approval, cancelling, finalizing, or
# holding an uncertain outcome. The session guard and the run scheduler
# share this set.
RUN_ACTIVE_STATUSES = (
    RUN_QUEUED,
    RUN_PREPARING,
    RUN_RUNNING,
    RUN_WAITING_APPROVAL,
    RUN_CANCELLING,
    RUN_FINALIZING,
    RUN_NEEDS_ATTENTION,
)

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

    Per-session sequence is FIFO execution order. Each successful Run publishes
    its native history in one PostgreSQL transaction before the next Run starts.
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
    initiated_by_id: Mapped[UUID | None]
    owner_ref: Mapped[str] = mapped_column(String(MAX_OWNER_REF_LENGTH))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("execution_sessions.id"))
    sequence: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default=RUN_QUEUED)
    request_ciphertext: Mapped[str] = mapped_column(Text)
    result_ciphertext: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(MAX_ERROR_CODE_LENGTH))
    event_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    capacity_reservation_id: Mapped[UUID | None]
    capacity_epoch: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
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
    referenced approval request belongs to platform approvals, which alone decides.
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
    token_reservation_id: Mapped[UUID | None]
    reconciliation_ciphertext: Mapped[str | None] = mapped_column(Text)
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_mcp_connections.id"), index=True
    )
    environment_id: Mapped[UUID | None]
