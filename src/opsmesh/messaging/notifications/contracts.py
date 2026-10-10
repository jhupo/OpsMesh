from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from opsmesh.shared.contracts import TimestampedModel
from opsmesh.shared.security.redaction import redact_sensitive_payload


class NotificationCreateRequest(BaseModel):
    notification_type: str = Field(min_length=1, max_length=80)
    severity: str = Field(default="info", min_length=1, max_length=32)
    source_type: str = Field(default="system", min_length=1, max_length=80)
    source_id: UUID | None = None
    title: str = Field(min_length=1, max_length=240)
    body: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class NotificationMarkReadRequest(BaseModel):
    notification_ids: list[UUID] | None = None
    include_archived: bool = False


class NotificationResponse(TimestampedModel):
    workspace_id: UUID
    notification_type: str
    severity: str
    source_type: str
    source_id: UUID | None
    title: str
    body: str
    metadata: dict[str, object] = Field(validation_alias="metadata_")
    read_at: datetime | None
    archived_at: datetime | None

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class NotificationMarkReadResponse(BaseModel):
    workspace_id: UUID
    updated_count: int


class NotificationPreferenceUpdateRequest(BaseModel):
    in_app_enabled: bool | None = None
    email_enabled: bool | None = None
    announcement_enabled: bool | None = None
    task_enabled: bool | None = None
    approval_enabled: bool | None = None
    security_enabled: bool | None = None

    @model_validator(mode="after")
    def _require_update(self) -> "NotificationPreferenceUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("at least one notification preference is required")
        return self


class NotificationPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    user_id: UUID
    in_app_enabled: bool
    email_enabled: bool
    announcement_enabled: bool
    task_enabled: bool
    approval_enabled: bool
    security_enabled: bool


class NotificationCountsResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    total_count: int
    unread_count: int
    read_count: int
    archived_count: int
    severity_counts: dict[str, int]
    type_counts: dict[str, int]
    source_type_counts: dict[str, int]
