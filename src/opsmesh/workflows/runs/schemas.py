"""Workflow invocation and control values."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: UUID
    engine: str = Field(default="temporal", pattern=r"^[a-z][a-z0-9_-]{0,23}$")
    request_key: str = Field(min_length=1, max_length=200)
    input: object


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    instance_id: UUID
    engine: str
    status: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class NodeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    run_id: UUID
    path: str
    kind: str
    status: str
    execution_run_id: UUID | None
    approval_id: UUID | None
    error_code: str | None


class SignalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=2000)
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    value: object


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: UUID
    allow: bool


class EffectResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=200)
    outcome: Literal["executed", "not_executed"]
    evidence: str = Field(min_length=1, max_length=4000)
