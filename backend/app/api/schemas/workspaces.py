from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    settings: dict[str, object] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["active", "paused", "disabled", "archived"] | None = None
    settings: dict[str, object] | None = None

    @field_validator("settings")
    @classmethod
    def _validate_scheduler_settings(
        cls,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if value is None:
            return value
        raw_scheduler = value.get("scheduler")
        if raw_scheduler is None:
            return value
        if not isinstance(raw_scheduler, dict):
            raise ValueError("scheduler settings must be an object")
        paused = raw_scheduler.get("paused")
        if paused is not None and not isinstance(paused, bool):
            raise ValueError("scheduler.paused must be a boolean")
        pause_reason = raw_scheduler.get("pause_reason")
        if pause_reason is not None and not isinstance(pause_reason, str):
            raise ValueError("scheduler.pause_reason must be a string")
        return value


class WorkspaceResponse(TimestampedModel):
    owner_user_id: UUID
    name: str
    slug: str
    status: str
    settings: dict[str, object]

    @field_serializer("settings")
    def _serialize_settings(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class WorkspaceMemberResponse(TimestampedModel):
    workspace_id: UUID
    user_id: UUID
    role: str
    status: str


class WorkspaceMemberCreateRequest(BaseModel):
    user_id: UUID
    role: Literal["owner", "admin", "operator", "viewer"]


class WorkspaceMemberUpdateRequest(BaseModel):
    role: Literal["owner", "admin", "operator", "viewer"] | None = None
    status: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def _require_update(self) -> "WorkspaceMemberUpdateRequest":
        if self.role is None and self.status is None:
            raise ValueError("role or status is required")
        return self


class WorkspaceInviteCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["owner", "admin", "operator", "viewer"]
    expires_at: datetime
    invitee_user_id: UUID | None = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("email must be a valid email address")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def _validate_future_expiry(cls, value: datetime) -> datetime:
        expires_at = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        if expires_at <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return value


class WorkspaceInviteAcceptRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)


class WorkspaceInviteResponse(TimestampedModel):
    workspace_id: UUID
    email: str
    role: str
    status: str
    fingerprint: str
    inviter_user_id: UUID | None
    invitee_user_id: UUID | None
    accepted_by_user_id: UUID | None
    revoked_by_user_id: UUID | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class WorkspaceInviteCreateResponse(WorkspaceInviteResponse):
    token: str


class WorkspaceInviteAcceptResponse(BaseModel):
    invite: WorkspaceInviteResponse
    member: WorkspaceMemberResponse


class WorkspaceQuotaUpsertItem(BaseModel):
    quota_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    limit_value: int = Field(ge=0)
    unit: str = Field(default="count", min_length=1, max_length=32)


class WorkspaceQuotaUpsertRequest(BaseModel):
    quotas: list[WorkspaceQuotaUpsertItem] = Field(min_length=1, max_length=64)


class WorkspaceQuotaResponse(TimestampedModel):
    workspace_id: UUID
    quota_key: str
    limit_value: int
    reserved_value: int
    unit: str
    status: str

    @computed_field
    @property
    def available_value(self) -> int:
        return max(self.limit_value - self.reserved_value, 0)

    @computed_field
    @property
    def utilization(self) -> float:
        if self.limit_value <= 0:
            return 0.0
        return round(self.reserved_value / self.limit_value, 4)

    @computed_field
    @property
    def saturated(self) -> bool:
        return self.limit_value > 0 and self.reserved_value >= self.limit_value

    @computed_field
    @property
    def over_reserved(self) -> bool:
        return self.reserved_value > self.limit_value


class WorkspaceExecutionSlotReservationResponse(TimestampedModel):
    id: UUID
    reservation_key: str
    task_id: UUID | None
    task_step_id: UUID | None
    agent_run_id: UUID | None
    resource_usage: dict[str, object]
    status: str
    expires_at: datetime | None
    released_at: datetime | None


class WorkspaceExecutionSlotSummaryResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    quotas: list[WorkspaceQuotaResponse]
    active_reservations: list[WorkspaceExecutionSlotReservationResponse]
    active_reservation_count: int
    reservation_usage: dict[str, int]
    over_reserved_quota_keys: list[str]
