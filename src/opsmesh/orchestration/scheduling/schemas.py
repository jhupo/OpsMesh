from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, model_validator

from opsmesh.runtime.temporal.contracts import ExecutionKind
from opsmesh.shared.contracts import FromAttributesContract
from opsmesh.shared.security.redaction import redact_sensitive_payload


class ScheduledJobScheduleRequest(BaseModel):
    type: str = Field(pattern="^(one_shot|hourly|daily)$")
    run_at: datetime | None = None
    minute: int | None = Field(default=None, ge=0, le=59)
    time_of_day: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}(:\d{2})?$")

    @model_validator(mode="after")
    def _validate_schedule(self) -> "ScheduledJobScheduleRequest":
        if self.type == "one_shot" and self.run_at is None:
            raise ValueError("One-shot schedules require run_at")
        if self.type == "daily" and self.time_of_day is None:
            raise ValueError("Daily schedules require time_of_day")
        return self

    def as_config(self) -> dict[str, object]:
        if self.type == "one_shot":
            return {"run_at": self.run_at.isoformat() if self.run_at is not None else None}
        if self.type == "hourly":
            return {"minute": 0 if self.minute is None else self.minute}
        return {"time_of_day": self.time_of_day}


class ScheduledJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    schedule: ScheduledJobScheduleRequest
    action_type: str = Field(default="record_due_action", pattern="^(execute|record_due_action)$")
    execution_kind: ExecutionKind | None = None
    resource_id: UUID | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class ScheduledJobResponse(FromAttributesContract):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    name: str
    schedule_type: str
    schedule_config: dict[str, object]
    status: str
    action_type: str
    execution_kind: str | None
    resource_id: UUID | None
    parameters: dict[str, object]
    revision: int
    metadata_: dict[str, object] = Field(serialization_alias="metadata")
    last_run_at: datetime | None
    next_run_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("schedule_config", "parameters", "metadata_")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
