from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, model_validator

from backend.app.api.schemas.common import ORMModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


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
    name: str = Field(min_length=1, max_length=160)
    schedule: ScheduledJobScheduleRequest
    action_type: str = Field(default="record_due_action", pattern="^(queue_job|record_due_action)$")
    job_type: str | None = Field(
        default=None,
        pattern=(
            "^(agent\\.run|mcp\\.tool_execution|task\\.plan|team\\.execution_loop|"
            "runtime\\.cleanup|workspace\\.archive_export|memory\\.index|webhook\\.delivery|"
            "secret\\.reencrypt|model_provider\\.health_check)$"
        ),
    )
    resource_id: UUID | None = None
    routing: dict[str, object] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=-100, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)
    metadata: dict[str, object] = Field(default_factory=dict)


class ScheduledJobResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    name: str
    schedule_type: str
    schedule_config: dict[str, object]
    status: str
    action_type: str
    job_type: str | None
    resource_id: UUID | None
    routing: dict[str, object]
    priority: int
    max_attempts: int
    metadata_: dict[str, object] = Field(serialization_alias="metadata")
    last_run_at: datetime | None
    next_run_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("schedule_config", "routing", "metadata_")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class ScheduledJobMaintenanceResponse(BaseModel):
    enqueued: int
    recorded: int
    skipped: int
