from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, field_validator

from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload

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


class RuntimeSpacePauseRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


class RuntimeSpaceForceReleaseRequest(BaseModel):
    reservation_key: str | None = Field(default=None, min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=240)


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

    @field_serializer("policy", "network_policy", "storage_policy", "cleanup_policy")
    def _serialize_policies(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RuntimeSpaceControlResponse(BaseModel):
    runtime_space: RuntimeSpaceResponse
    cleared_blocked_steps: int = 0


class RuntimeSpaceForceReleaseResponse(BaseModel):
    runtime_space: RuntimeSpaceResponse
    released_reservations: int


class RuntimeSpaceQuotaDiagnosticResponse(BaseModel):
    quota_key: str
    limit_value: int
    reserved_value: int
    unit: str
    utilization: float
    saturated: bool


class RuntimeSpaceReservationDiagnosticResponse(BaseModel):
    id: UUID
    reservation_key: str
    task_id: UUID | None
    task_step_id: UUID | None
    agent_run_id: UUID | None
    resource_usage: dict[str, object]
    created_at: datetime
    expires_at: datetime | None


class RuntimeSpaceRuntimeDiagnosticResponse(BaseModel):
    id: UUID
    name: str
    runtime_provider: str
    runtime_type: str
    status: str
    connection_status: str
    has_docker_container: bool
    limits: dict[str, object]
    network_policy: dict[str, object]
    capabilities: dict[str, object]
    last_heartbeat_at: datetime | None

    @field_serializer("limits", "network_policy", "capabilities")
    def _serialize_runtime_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RuntimeSpaceBlockedStepDiagnosticResponse(BaseModel):
    task_step_id: UUID
    task_id: UUID
    task_title: str
    step_title: str
    reason: str
    code: str
    message: str
    resource_key: str | None = None
    blocked_resource_keys: list[str] = Field(default_factory=list)
    priority_score: int | None = None
    created_at: datetime


class RuntimeSpaceDiagnosticsResponse(BaseModel):
    runtime_space: RuntimeSpaceResponse
    quotas: list[RuntimeSpaceQuotaDiagnosticResponse]
    active_reservations: list[RuntimeSpaceReservationDiagnosticResponse]
    runtimes: list[RuntimeSpaceRuntimeDiagnosticResponse]
    blocked_steps: list[RuntimeSpaceBlockedStepDiagnosticResponse]


class RuntimeSpaceEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    runtime_space_id: UUID
    event_type: str
    message: str
    event_metadata: dict[str, object]
    created_at: datetime

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
