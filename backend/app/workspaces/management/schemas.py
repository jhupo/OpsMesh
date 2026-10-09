from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    field_serializer,
    field_validator,
)

from backend.app.governance.reviews.approval_config import approval_configuration
from backend.app.shared.contracts import TimestampedModel
from backend.app.shared.security.redaction import redact_sensitive_payload


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    settings: dict[str, object] = Field(default_factory=dict)

    @field_validator("settings")
    @classmethod
    def validate_settings(cls, value: dict[str, object]) -> dict[str, object]:
        _validate_scheduler_settings(value)
        _validate_resource_review_settings(value)
        return value


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
    if "resource_review" in settings:
        raise ValueError(
            "resource_review was replaced by approvals; migrate settings before saving"
        )
    approval_configuration(settings)


class WorkspaceResponse(TimestampedModel):
    owner_user_id: UUID
    name: str
    slug: str
    status: str
    settings: dict[str, object]

    @field_serializer("settings")
    def _serialize_settings(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


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
