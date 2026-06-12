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
from backend.app.reviews.constants import (
    DEFAULT_RESOURCE_REVIEW_MODEL,
    MODEL_REQUEST_REVIEW_SETTINGS_KEY,
    PRIVATE_RESOURCE_REVIEW_SETTINGS_KEY,
    PUBLIC_RESOURCE_REVIEW_SETTINGS_KEY,
    RESOURCE_REVIEW_SETTINGS_KEY,
    SEMANTIC_REVIEW_SETTINGS_KEY,
)

_RESOURCE_REVIEW_SCOPE_KEYS = frozenset(
    {
        "agent_profile",
        "skill",
        "mcp_server",
        "mcp_tool_allowlist",
        "mcp_credential_reference",
        "plugin",
        "capability",
    }
)


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
    def _validate_settings(
        cls,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if value is None:
            return value
        _validate_scheduler_settings(value)
        _validate_resource_review_settings(value)
        return value


def _validate_scheduler_settings(settings: dict[str, object]) -> None:
    raw_scheduler = settings.get("scheduler")
    if raw_scheduler is None:
        return
    if not isinstance(raw_scheduler, dict):
        raise ValueError("scheduler settings must be an object")
    paused = raw_scheduler.get("paused")
    if paused is not None and not isinstance(paused, bool):
        raise ValueError("scheduler.paused must be a boolean")
    pause_reason = raw_scheduler.get("pause_reason")
    if pause_reason is not None and not isinstance(pause_reason, str):
        raise ValueError("scheduler.pause_reason must be a string")


def _validate_resource_review_settings(settings: dict[str, object]) -> None:
    raw_resource_review = settings.get(RESOURCE_REVIEW_SETTINGS_KEY)
    if raw_resource_review is None:
        return
    if not isinstance(raw_resource_review, dict):
        raise ValueError("resource_review settings must be an object")
    _validate_model_request_review_settings(raw_resource_review)
    _validate_resource_review_scope_settings(raw_resource_review)
    raw_semantic = raw_resource_review.get(SEMANTIC_REVIEW_SETTINGS_KEY)
    if raw_semantic is None:
        return
    if not isinstance(raw_semantic, dict):
        raise ValueError("resource_review.semantic_review must be an object")
    enabled = raw_semantic.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("resource_review.semantic_review.enabled must be a boolean")
    credential_id = raw_semantic.get("model_provider_credential_id")
    if credential_id not in (None, ""):
        try:
            UUID(str(credential_id))
        except ValueError as exc:
            raise ValueError(
                "resource_review.semantic_review.model_provider_credential_id must be a UUID"
            ) from exc
    model = raw_semantic.get("model")
    if model is None:
        raw_semantic["model"] = DEFAULT_RESOURCE_REVIEW_MODEL
    elif not isinstance(model, str) or not model.strip():
        raise ValueError("resource_review.semantic_review.model must be a non-empty string")
    timeout_seconds = raw_semantic.get("timeout_seconds")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int | float)
        or not 1 <= timeout_seconds <= 120
    ):
        raise ValueError(
            "resource_review.semantic_review.timeout_seconds must be between 1 and 120"
        )
    fail_closed = raw_semantic.get("fail_closed")
    if fail_closed is not None and not isinstance(fail_closed, bool):
        raise ValueError("resource_review.semantic_review.fail_closed must be a boolean")
    if fail_closed is False:
        raise ValueError("resource_review.semantic_review.fail_closed must remain true")


def _validate_resource_review_scope_settings(resource_review: dict[str, object]) -> None:
    for scope_key in (
        PRIVATE_RESOURCE_REVIEW_SETTINGS_KEY,
        PUBLIC_RESOURCE_REVIEW_SETTINGS_KEY,
    ):
        raw_scope = resource_review.get(scope_key)
        if raw_scope is None:
            continue
        if not isinstance(raw_scope, dict):
            raise ValueError(f"resource_review.{scope_key} must be an object")
        unknown = sorted(set(raw_scope) - _RESOURCE_REVIEW_SCOPE_KEYS)
        if unknown:
            raise ValueError(
                f"resource_review.{scope_key} has unsupported resource types: "
                f"{', '.join(unknown)}"
            )
        for resource_type, enabled in raw_scope.items():
            if not isinstance(enabled, bool):
                raise ValueError(
                    f"resource_review.{scope_key}.{resource_type} must be a boolean"
                )
            if scope_key == PUBLIC_RESOURCE_REVIEW_SETTINGS_KEY and not enabled:
                raise ValueError(
                    f"resource_review.{scope_key}.{resource_type} cannot be disabled"
                )


def _validate_model_request_review_settings(resource_review: dict[str, object]) -> None:
    raw_model_request = resource_review.get(MODEL_REQUEST_REVIEW_SETTINGS_KEY)
    if raw_model_request is None:
        return
    if not isinstance(raw_model_request, dict):
        raise ValueError("resource_review.model_request_review must be an object")
    semantic_mode = raw_model_request.get("semantic_mode")
    if semantic_mode is None:
        raw_model_request["semantic_mode"] = "always"
        return
    if semantic_mode != "always":
        raise ValueError(
            "resource_review.model_request_review.semantic_mode must be always"
        )


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


class WorkspaceHealthResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    status: str
    score: int
    summary: dict[str, object]
    risk_items: list[dict[str, object]]
    recommended_actions: list[str]
    trend_basis: dict[str, object]

    @field_serializer("summary", "trend_basis")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("risk_items")
    def _serialize_risk_items(self, value: list[dict[str, object]]) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class WorkspaceHealthSnapshotResponse(TimestampedModel):
    workspace_id: UUID
    status: str
    score: int
    summary: dict[str, object]
    risk_items: list[dict[str, object]]
    recommended_actions: list[str]
    trend_basis: dict[str, object]

    @field_serializer("summary", "trend_basis")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("risk_items")
    def _serialize_snapshot_risk_items(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class WorkspaceHealthTrendResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    snapshot_count: int
    compared_snapshot_count: int
    latest: dict[str, object] | None
    previous: dict[str, object] | None
    score_delta: int | None
    status_change: dict[str, object] | None
    risk_changes: dict[str, object]
    recommendation_changes: dict[str, object]

    @field_serializer(
        "latest",
        "previous",
        "status_change",
        "risk_changes",
        "recommendation_changes",
    )
    def _serialize_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None
