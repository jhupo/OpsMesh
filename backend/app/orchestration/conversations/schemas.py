from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.shared.contracts import TimestampedModel
from backend.app.shared.security.redaction import redact_sensitive_text


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="New conversation", min_length=1, max_length=240)
    mode: Literal["auto", "agent", "team"] = "auto"
    agent_profile_id: UUID | None = None
    agent_team_id: UUID | None = None
    runtime_space_id: UUID | None = None
    workspace_project_id: UUID | None = None

    orchestration_definition_id: UUID | None = None
    orchestration_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_target(self) -> "ConversationCreate":
        if self.orchestration_definition_id is not None and self.mode != "team":
            raise ValueError("Workflow execution requires team mode")
        if self.orchestration_version is not None and self.orchestration_definition_id is None:
            raise ValueError("Workflow version requires a definition")
        if self.mode == "team":
            if self.agent_team_id is None or self.agent_profile_id is not None:
                raise ValueError("Team mode requires only an agent_team_id")
        elif self.agent_team_id is not None or (
            self.mode == "agent" and self.agent_profile_id is None
        ):
            raise ValueError("Agent mode requires an agent_profile_id; teams require team mode")
        return self


class ConversationResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID
    title: str
    mode: str
    agent_profile_id: UUID | None
    agent_team_id: UUID | None
    runtime_space_id: UUID | None
    workspace_project_id: UUID | None
    orchestration_definition_id: UUID | None
    orchestration_version: int | None


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=32000)

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value


class TurnResponse(TimestampedModel):
    conversation_id: UUID
    sequence: int
    body: str
    reply: str | None
    status: str
    error_code: str | None
    round: int

    @field_serializer("body", "reply")
    def redact(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) if value is not None else None


class ExecutionResponse(TimestampedModel):
    turn_id: UUID
    task_id: UUID
    purpose: str
    parent_run_id: UUID | None
    round: int


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    turn_id: UUID
    status: str
    round: int
    created_at: datetime
