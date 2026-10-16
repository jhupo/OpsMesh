"""Security event filter and sanitized response contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SecurityEventView(BaseModel):
    """One sanitized security fact; details never carry credentials."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    kind: str
    severity: str
    summary: str
    details: dict[str, object]
    user_id: UUID | None
    occurred_at: datetime
    created_at: datetime


class SecurityEventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[SecurityEventView]
    total: int
