"""FIFO Run receipts, approvals, signal identities and external effects."""

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExecutionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "execution_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key"),
        UniqueConstraint("session_id", "sequence"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    revision_id: Mapped[UUID | None] = mapped_column(index=True)
    instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_instances.id"), index=True
    )
    driver: Mapped[str] = mapped_column(String(24), default="direct")
    attempt_id: Mapped[str | None] = mapped_column(String(100))
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("execution_sessions.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    generation: Mapped[int | None] = mapped_column(Integer)
    environment_id: Mapped[UUID | None] = mapped_column(ForeignKey("sandbox_environments.id"))
    request_key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    encrypted_input: Mapped[str] = mapped_column(Text)
    encrypted_prepared: Mapped[str | None] = mapped_column(Text)
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    encrypted_state: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[UUID | None]
    approval_decision: Mapped[bool | None] = mapped_column(Boolean)
    approved_calls: Mapped[list[str]] = mapped_column(JSONB, default=list)
    key_id: Mapped[str] = mapped_column(String(100))
    capacity_id: Mapped[UUID | None] = mapped_column(ForeignKey("user_capacity_reservations.id"))
    error_code: Mapped[str | None] = mapped_column(String(100))


class ExecutionOperation(TimestampMixin, Base):
    __tablename__ = "execution_operations"

    id: Mapped[str] = mapped_column(String(500), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("execution_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    reservation_id: Mapped[UUID | None] = mapped_column(ForeignKey("token_reservations.id"))
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str | None] = mapped_column(String(100))
    reconciliation: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class ExecutionEvent(TimestampMixin, Base):
    __tablename__ = "execution_events"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("execution_runs.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100))


ACTIVE = ("queued", "running", "interrupted", "cancelling", "needs_attention")
