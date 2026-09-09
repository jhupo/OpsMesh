from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, computed_field, field_validator, model_validator

from backend.app.api.schemas.common import ORMModel
from backend.app.security.redaction import redact_sensitive_payload_item


class WebhookSubscriptionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_url: HttpUrl
    event_types: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=50)
    signing_secret: str = Field(min_length=16, max_length=4096)

    @field_validator("event_types")
    @classmethod
    def normalize_event_types(cls, value: list[str]) -> list[str]:
        return _normalize_event_types(value)


class WebhookSubscriptionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    target_url: HttpUrl | None = None
    event_types: list[str] | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("event_types")
    @classmethod
    def normalize_event_types(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_event_types(value)

    @model_validator(mode="after")
    def require_update_field(self) -> "WebhookSubscriptionUpdateRequest":
        if self.name is None and self.target_url is None and self.event_types is None:
            raise ValueError("At least one subscription field is required")
        return self


class WebhookSigningSecretRotateRequest(BaseModel):
    signing_secret: str = Field(min_length=16, max_length=4096)


class WebhookSubscriptionResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    name: str
    target_url: str
    event_types: list[str]
    signing_secret_fingerprint: str
    encryption_key_id: str
    status: str
    disabled_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_message: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]  # Pydantic documented mypy limitation.
    @property
    def signing_secret(self) -> str:
        return "[redacted]"


class WebhookDeliveryAttemptResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    subscription_id: UUID
    event_id: str
    event_type: str
    status: str
    attempt_count: int
    max_attempts: int
    available_at: datetime
    queued_at: datetime | None
    delivered_at: datetime | None
    dead_lettered_at: datetime | None
    next_retry_at: datetime | None
    last_error: str | None
    last_status_code: int | None
    response_body_snippet: str | None
    dead_letter_metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime

    @field_validator("dead_letter_metadata")
    @classmethod
    def redact_dead_letter_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        redacted = redact_sensitive_payload_item(value)
        return redacted if isinstance(redacted, dict) else {}


def _normalize_event_types(value: list[str]) -> list[str]:
    normalized = sorted({item.strip() for item in value if item.strip()})
    if not normalized:
        raise ValueError("At least one event type is required")
    if "*" in normalized and len(normalized) > 1:
        return ["*"]
    return normalized
