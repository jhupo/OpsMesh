from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_serializer

from opsmesh.shared.contracts import FromAttributesContract
from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_text_fragments


class RuntimeEventEvidenceResponse(FromAttributesContract):
    id: UUID
    workspace_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
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
    def redact_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("message")
    def redact_message(self, value: str) -> str:
        return redact_text_fragments(value)


class SecurityEventResponse(FromAttributesContract):
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
    def redact_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("reason")
    def redact_reason(self, value: str) -> str:
        return redact_text_fragments(value)


class EvidenceCorrelationResponse(BaseModel):
    workspace_id: UUID
    trace_ids: list[str]
    request_ids: list[str]
    task_ids: list[UUID]
    run_ids: list[UUID]
    worker_ids: list[str]
    runtime_ids: list[str]
    evidence_counts: dict[str, int]
    sampled_sources: list[str]
    drilldowns: dict[str, str]
