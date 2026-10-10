from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

from opsmesh.shared.contracts import TimestampedModel
from opsmesh.workspaces.members.admin_schemas import AdminWorkspaceMemberResponse


class AdminUserResponse(TimestampedModel):
    username: str | None
    email: str
    display_name: str
    status: str
    platform_admin: bool


class AdminUserListResponse(AdminUserResponse):
    invitation_delivery_status: str | None = None
    workspace_count: int = 0
    active_workspace_count: int = 0
    resource_usage_rate: float = Field(default=0.0, ge=0.0)


class AdminUserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=1, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=4096)
    platform_admin: bool = False

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("email must contain @")
        return normalized

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name is required")
        return normalized


class AdminUserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=1, max_length=80)
    platform_admin: bool | None = None

    @model_validator(mode="after")
    def _require_update(self) -> "AdminUserUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("at least one user field is required")
        return self


class AdminUserCreateResponse(AdminUserResponse):
    initial_password: str


class AdminUserTokenRevokeResponse(BaseModel):
    revoked: int


class AdminUserPasswordResetResponse(AdminUserResponse):
    temporary_password: str


class AdminUserDetailResponse(AdminUserResponse):
    workspace_memberships: list[AdminWorkspaceMemberResponse]


class AdminUserStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"]
