from __future__ import annotations

from pydantic import BaseModel

from backend.app.api.schemas.platform.audit import AuditEventResponse
from backend.app.domains.orchestration.runs.contracts import RunEventResponse
from backend.app.runtime.operations.contracts.events import SecurityEventResponse


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
