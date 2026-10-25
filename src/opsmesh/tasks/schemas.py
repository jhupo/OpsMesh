from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import Field, field_validator, model_validator

from opsmesh.shared.schemas import RecordView, RequestSchema


class CreateTaskRequest(RequestSchema):
    agent_id: str
    input: Any
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    timeout_seconds: int = Field(default=600, ge=1, le=86400)
    session_id: str | None = None


class InvokePublishedAgentRequest(RequestSchema):
    input: Any
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    timeout_seconds: int = Field(default=600, ge=1, le=86400)
    session_id: str | None = None


class RespondToInteractionRequest(RequestSchema):
    value: Any


class CreateScheduleRequest(RequestSchema):
    agent_id: str
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["once", "interval", "cron"]
    input: Any
    expression: str | None = Field(default=None, max_length=100)
    interval_seconds: int | None = Field(default=None, ge=10, le=31536000)
    timezone: str = Field(default="UTC", max_length=100)
    first_at: datetime

    @field_validator("first_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def validate_trigger(self) -> "CreateScheduleRequest":
        if self.kind == "once" and (
            self.expression is not None or self.interval_seconds is not None
        ):
            raise ValueError("One-time schedules only accept first_at")
        if self.kind == "interval" and (
            self.interval_seconds is None or self.expression is not None
        ):
            raise ValueError("Interval schedules require interval_seconds")
        if self.kind == "cron" and (
            not self.expression
            or len(self.expression.split()) != 5
            or not croniter.is_valid(self.expression)
            or self.interval_seconds is not None
        ):
            raise ValueError("Cron schedules require a valid five-field expression")
        return self


class UpdateScheduleRequest(RequestSchema):
    enabled: bool


class TaskView(RecordView):
    owner_id: str
    session_id: str | None
    session_revision: int | None
    agent_id: str | None
    parent_id: str | None
    waiting_child_id: str | None
    schedule_id: str | None
    status: str
    idempotency_key: str | None
    attempt_count: int
    timeout_seconds: int
    cancel_requested: bool
    checkpoint_id: str | None
    error_code: str | None
    finished_at: datetime | None


class TaskDetailsView(TaskView):
    result: Any = None


class TaskAttemptView(RecordView):
    task_id: str
    number: int
    worker_id: str
    trace_id: str
    status: str
    lease_until: datetime
    finished_at: datetime | None


class InteractionReceiptView(RecordView):
    task_id: str
    recipient_id: str
    kind: str
    responder_id: str | None
    status: str
    expires_at: datetime


class InteractionView(InteractionReceiptView):
    prompt: str
    details: dict[str, Any]


class ScheduleView(RecordView):
    owner_id: str
    agent_id: str
    name: str
    kind: str
    expression: str | None
    interval_seconds: int | None
    timezone: str
    next_at: datetime
    enabled: bool
    last_task_id: str | None
    error_code: str | None
