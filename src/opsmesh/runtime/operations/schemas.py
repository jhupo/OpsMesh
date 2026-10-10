from __future__ import annotations

from pydantic import BaseModel

from opsmesh.governance.audit.chain_schemas import AuditEventResponse
from opsmesh.orchestration.runs.contracts import RunEventResponse
from opsmesh.runtime.operations.contracts.events import SecurityEventResponse


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
