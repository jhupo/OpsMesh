from __future__ import annotations

from pydantic import BaseModel

from backend.app.api.schemas.operations.events import SecurityEventResponse
from backend.app.api.schemas.orchestration.runs import RunEventResponse
from backend.app.api.schemas.platform.audit import AuditEventResponse


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
