"""Contracts for bounded platform commands."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlatformOperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=120)
    target: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)


class PlatformOperationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    idempotency_key: str
    owner: str
    command: str
    target: dict[str, object]
    status: str
    result: dict[str, object]
    failure_code: str | None
    requested_by_user_id: UUID
    error_detail: str | None


class PlatformAdmissionView(BaseModel):
    drained: bool
    active: dict[str, int]
