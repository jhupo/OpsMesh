"""Workflow records are distinct from independently owned Agent execution runs."""

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ACTIVE = ("queued", "running", "cancelling", "needs_attention")


class WorkflowRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (UniqueConstraint("user_id", "request_key"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    instance_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_instances.id"), index=True)
    engine: Mapped[str] = mapped_column(String(24))
    request_key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    encrypted_input: Mapped[str] = mapped_column(Text)
    encrypted_context: Mapped[str | None] = mapped_column(Text)
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))


class WorkflowNodeRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_node_runs"
    __table_args__ = (UniqueConstraint("run_id", "path"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    path: Mapped[str] = mapped_column(String(2000))
    kind: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    execution_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_runs.id"), index=True
    )
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    encrypted_descriptor: Mapped[str | None] = mapped_column(Text)
    encrypted_input: Mapped[str] = mapped_column(Text)
    approval_id: Mapped[UUID | None]
    approval_decision: Mapped[bool | None] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(100))


class WorkflowOperation(TimestampMixin, Base):
    __tablename__ = "workflow_operations"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    node_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_node_runs.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    encrypted_reconciliation: Mapped[str | None] = mapped_column(Text)


class WorkflowEvent(TimestampMixin, Base):
    __tablename__ = "workflow_events"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))


class WorkflowSignal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_signals"
    __table_args__ = (UniqueConstraint("run_id", "path", "name"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    path: Mapped[str] = mapped_column(String(2000))
    name: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
