from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from backend.app.api.schemas.common import ORMModel, TimestampedModel

RuntimeSpaceScope = Literal["workspace", "team", "task"]
RuntimeSpaceStatus = Literal["active", "paused", "disabled", "quarantined", "archived"]


def default_network_policy() -> dict[str, object]:
    return {"mode": "none"}


class RuntimeSpaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    scope: RuntimeSpaceScope = "workspace"
    target_id: UUID | None = None
    default_runtime_template_id: UUID | None = None
    policy: dict[str, object] = Field(default_factory=dict)
    network_policy: dict[str, object] = Field(default_factory=default_network_policy)
    storage_policy: dict[str, object] = Field(default_factory=dict)
    cleanup_policy: dict[str, object] = Field(default_factory=dict)
    quota_limits: dict[str, int] = Field(default_factory=dict)

    @field_validator("quota_limits")
    @classmethod
    def _validate_quota_limits(cls, value: dict[str, int]) -> dict[str, int]:
        for key, limit in value.items():
            if not key or len(key) > 80:
                raise ValueError("quota key must be between 1 and 80 characters")
            if limit < 0:
                raise ValueError("quota limit must be non-negative")
        return value


class RuntimeSpaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: RuntimeSpaceStatus | None = None
    policy: dict[str, object] | None = None
    network_policy: dict[str, object] | None = None
    storage_policy: dict[str, object] | None = None
    cleanup_policy: dict[str, object] | None = None
    quota_limits: dict[str, int] | None = None

    @field_validator("quota_limits")
    @classmethod
    def _validate_quota_limits(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return value
        for key, limit in value.items():
            if not key or len(key) > 80:
                raise ValueError("quota key must be between 1 and 80 characters")
            if limit < 0:
                raise ValueError("quota limit must be non-negative")
        return value


class RuntimeSpaceResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    default_runtime_template_id: UUID | None
    name: str
    scope: str
    status: str
    policy: dict[str, object]
    network_policy: dict[str, object]
    storage_policy: dict[str, object]
    cleanup_policy: dict[str, object]


class RuntimeSpaceEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    runtime_space_id: UUID
    event_type: str
    message: str
    event_metadata: dict[str, object]
    created_at: datetime
