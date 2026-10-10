from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

from opsmesh.shared.contracts import TimestampedModel


class AdminAnnouncementCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(default="", max_length=100_000)
    severity: Literal["info", "warning", "critical"] = "info"
    workspace_ids: list[UUID] = Field(default_factory=list, max_length=500)
    roles: list[str] = Field(default_factory=list, max_length=8)
    user_ids: list[UUID] = Field(default_factory=list, max_length=2_000)

    @field_validator("roles")
    @classmethod
    def _validate_roles(cls, value: list[str]) -> list[str]:
        allowed = {"owner", "admin", "operator", "viewer"}
        normalized = sorted({item.strip().lower() for item in value if item.strip()})
        invalid = [item for item in normalized if item not in allowed]
        if invalid:
            raise ValueError(f"unsupported workspace roles: {', '.join(invalid)}")
        return normalized

    @model_validator(mode="after")
    def _require_audience(self) -> "AdminAnnouncementCreateRequest":
        if not self.workspace_ids and not self.roles and not self.user_ids:
            raise ValueError("announcement audience is required")
        return self


class AdminAnnouncementResponse(TimestampedModel):
    created_by_user_id: UUID | None
    title: str
    body: str
    severity: str
    status: str
    audience: dict[str, object]
    published_at: datetime | None
    retracted_at: datetime | None
    recipient_count: int
    read_count: int
    workspace_ids: list[UUID]
