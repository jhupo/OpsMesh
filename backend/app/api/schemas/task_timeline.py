from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload


class TaskTimelineAgent(BaseModel):
    id: UUID
    name: str
    role: str
    status: str


class TaskTimelineEvent(BaseModel):
    occurred_at: datetime
    source_type: str
    event_type: str
    phase: str
    status: str
    title: str
    summary: str
    task_step_id: UUID | None
    agent_run_id: UUID | None
    agent_profile_id: UUID | None
    artifact_id: UUID | None
    sequence: int
    agent: TaskTimelineAgent | None
    metadata: dict[str, object]

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskTimelineResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    summary: dict[str, object]
    events: list[TaskTimelineEvent]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class TaskEventFeedEvent(TaskTimelineEvent):
    cursor: int
    event_id: str


class TaskEventFeedResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    summary: dict[str, object]
    events: list[TaskEventFeedEvent]

    @field_serializer("summary")
    def _serialize_summary(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
