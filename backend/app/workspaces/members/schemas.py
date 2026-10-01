from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

from backend.app.shared.contracts import TimestampedModel


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
