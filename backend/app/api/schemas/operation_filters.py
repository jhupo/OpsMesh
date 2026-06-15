from __future__ import annotations

from pydantic import BaseModel

from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.operation_events import SecurityEventResponse
from backend.app.api.schemas.runs import RunEventResponse


class AuditEventFilterResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int


class RunEventFilterResponse(BaseModel):
    items: list[RunEventResponse]
    total: int
    limit: int
    offset: int


class SecurityEventFilterResponse(BaseModel):
    items: list[SecurityEventResponse]
    total: int
    limit: int
    offset: int
