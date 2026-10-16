"""User profile, roles and account status HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from opsmesh.identity.users.models import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_WORK_ROOT_LENGTH,
    USER_STATUSES,
)


class UserProfileView(BaseModel):
    """One account profile; avatar bytes are fetched from their own route."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    email: EmailStr
    display_name: str
    status: str
    platform_roles: list[str]
    work_root: str | None
    has_avatar: bool
    avatar_content_type: str | None
    avatar_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UpdateProfileRequest(BaseModel):
    """The fields an account may change on its own profile."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(
        default=None, min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH
    )
    work_root: str | None = Field(default=None, max_length=MAX_WORK_ROOT_LENGTH)


class AdminUpdateUserRequest(BaseModel):
    """The administrator-writable fields of one account."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(
        default=None, min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH
    )
    work_root: str | None = Field(default=None, max_length=MAX_WORK_ROOT_LENGTH)
    status: str | None = Field(default=None, max_length=20)
    platform_roles: list[str] | None = Field(default=None, max_length=8)


class UserPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[UserProfileView]
    total: int


def is_known_status(status: str) -> bool:
    return status in USER_STATUSES
