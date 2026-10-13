from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_text_fragments


class TeamTimelineEvent(BaseModel):
    id: str
    source_type: str
    event_type: str
    occurred_at: datetime
    resource_id: str
    message: str
    metadata: dict[str, object]

    @field_validator("metadata")
    @classmethod
    def redact_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value, text_mode="fragments")

    @field_validator("message")
    @classmethod
    def redact_message(cls, value: str) -> str:
        return redact_text_fragments(value)


class TeamTimelineSummary(BaseModel):
    total_events: int
    returned_events: int
    source_counts: dict[str, int]
    event_type_counts: dict[str, int]
    include_runs: bool


class TeamTimelineResponse(BaseModel):
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    limit: int
    offset: int
    summary: TeamTimelineSummary
    items: list[TeamTimelineEvent]
