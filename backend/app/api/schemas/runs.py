from datetime import datetime
from uuid import UUID

from pydantic import field_serializer

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class AgentRunResponse(TimestampedModel):
    workspace_id: UUID
    task_id: UUID | None
    task_step_id: UUID | None
    agent_profile_id: UUID | None
    runtime_id: UUID | None
    runtime_space_id: UUID | None
    status: str
    input: dict[str, object]
    output: dict[str, object] | None
    error: dict[str, object] | None
    model: str | None
    started_at: datetime | None
    completed_at: datetime | None

    @field_serializer("input")
    def _serialize_input(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("output")
    def _serialize_output(self, value: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None

    @field_serializer("error")
    def _serialize_error(self, value: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class RunEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    agent_run_id: UUID
    event_type: str
    sequence: int
    message: str
    event_metadata: dict[str, object]
    created_at: datetime

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
