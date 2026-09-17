from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.core.contracts import ORMModel
from backend.app.core.security.redaction import redact_sensitive_payload


class RuntimeEventResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    workspace_runtime_id: UUID
    event_type: str
    message: str
    request_id: str | None
    trace_id: str | None
    span_id: str | None
    worker_id: str | None
    runtime_id: str | None
    event_metadata: dict[str, object]
    created_at: datetime

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class SecurityEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID | None
    user_id: UUID | None
    action: str
    outcome: str
    severity: str
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    path: str
    method: str
    reason: str
    event_metadata: dict[str, object]
    created_at: datetime

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class OperationsCorrelationResponse(BaseModel):
    workspace_id: UUID
    trace_ids: list[str]
    request_ids: list[str]
    task_ids: list[UUID]
    run_ids: list[UUID]
    worker_ids: list[str]
    runtime_ids: list[str]
    evidence_counts: dict[str, int]
    drilldowns: dict[str, str]


class TeamRuntimeTimelineEventResponse(BaseModel):
    id: str
    source_type: str
    event_type: str
    occurred_at: datetime
    resource_id: str
    message: str
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TeamRuntimeTimelineSummaryResponse(BaseModel):
    total_events: int
    returned_events: int
    source_counts: dict[str, int]
    event_type_counts: dict[str, int]
    include_runs: bool
    include_queue: bool


class TeamRuntimeTimelineResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    limit: int
    offset: int
    summary: TeamRuntimeTimelineSummaryResponse
    items: list[TeamRuntimeTimelineEventResponse]
