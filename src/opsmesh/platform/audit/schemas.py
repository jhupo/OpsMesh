"""Sanitized audit responses and cursor pagination."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    sequence: int
    occurred_at: datetime
    recorded_at: datetime
    category: str
    severity: str
    schema_version: int
    operation_id: str | None
    correlation_id: str | None
    causation_id: UUID | None
    trace_id: str | None
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str
    target_id: str
    summary: str
    details: dict[str, object]
    evidence_refs: list[str]
    source: str


class AuditEventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[AuditEventView]
    next_after_sequence: int | None
