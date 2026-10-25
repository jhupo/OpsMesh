from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.infra.database import Base, Record


class Task(Record, Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    session_revision: Mapped[int | None] = mapped_column()
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agent_instances.id"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    waiting_child_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    schedule_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    encrypted_input: Mapped[str] = mapped_column(Text)
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    encrypted_source: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    attempt_count: Mapped[int] = mapped_column(default=0)
    event_sequence: Mapped[int] = mapped_column(default=0, server_default="0")
    event_bytes: Mapped[int] = mapped_column(default=0, server_default="0")
    timeout_seconds: Mapped[int] = mapped_column(default=600)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    checkpoint_id: Mapped[str | None] = mapped_column(String(36))
    decision_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(100))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskEvent(Record, Base):
    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "sequence"),)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    sequence: Mapped[int] = mapped_column()
    event: Mapped[str] = mapped_column(String(100))
    encrypted_data: Mapped[str] = mapped_column(Text)


class TaskAttempt(Record, Base):
    __tablename__ = "attempts"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    number: Mapped[int] = mapped_column()
    worker_id: Mapped[str] = mapped_column(String(100))
    trace_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskCheckpoint(Record, Base):
    __tablename__ = "checkpoints"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"))
    sdk_version: Mapped[str] = mapped_column(String(40))
    encrypted_state: Mapped[str] = mapped_column(Text)
    sandbox_generation: Mapped[int | None] = mapped_column()


class TaskInteraction(Record, Base):
    __tablename__ = "interactions"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    checkpoint_id: Mapped[str] = mapped_column(ForeignKey("checkpoints.id"))
    recipient_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    encrypted_prompt: Mapped[str] = mapped_column(Text)
    encrypted_decision: Mapped[str | None] = mapped_column(Text)
    responder_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskSchedule(Record, Base):
    __tablename__ = "schedules"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent_instances.id"))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    expression: Mapped[str | None] = mapped_column(String(100))
    interval_seconds: Mapped[int | None] = mapped_column()
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    encrypted_input: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_task_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(100))
