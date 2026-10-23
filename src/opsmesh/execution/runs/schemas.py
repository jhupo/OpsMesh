"""Public run requests reference persistent instances."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: UUID
    session_id: UUID | None = None
    driver: str = Field(default="direct", pattern=r"^[a-z][a-z0-9_-]{0,23}$")
    request_key: str = Field(min_length=1, max_length=200)
    input: object
    max_turns: int = Field(default=32, ge=1, le=256)
    timeout_seconds: int = Field(default=600, ge=1, le=604800)


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    instance_id: UUID | None
    session_id: UUID | None
    driver: str
    sequence: int | None
    status: str
    error_code: str | None
    approval_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: UUID
    allow: bool
