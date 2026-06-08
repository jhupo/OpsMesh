from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


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
