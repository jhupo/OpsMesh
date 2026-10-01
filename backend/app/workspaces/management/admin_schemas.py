from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    field_serializer,
)

from backend.app.shared.contracts import TimestampedModel
from backend.app.shared.security.redaction import redact_sensitive_payload


class AdminWorkspaceResponse(TimestampedModel):
    owner_user_id: UUID
    name: str
    slug: str
    status: str
    settings: dict[str, object]
    member_count: int
    project_count: int

    @field_serializer("settings")
    def _serialize_settings(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminWorkspaceStatusUpdateRequest(BaseModel):
    status: Literal["active", "paused", "disabled", "archived"]
    reason: str = "Updated by platform admin"
