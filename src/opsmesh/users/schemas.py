"""User profile, roles and account status HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from opsmesh.users.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from opsmesh.users.models import (
    MAX_DISPLAY_NAME_LENGTH,
    UserProfile,
)


class UserProfileView(BaseModel):
    """One account profile; avatar bytes are fetched from their own route."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    email: EmailStr
    display_name: str
    status: str
    platform_roles: list[str]
    has_avatar: bool
    avatar_content_type: str | None
    avatar_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UpdateProfileRequest(BaseModel):
    """The fields an account may change on its own profile."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)


class AdminCreateUserRequest(BaseModel):
    """Administrator-supplied account fields; no login session is issued."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)
    password: SecretStr = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    platform_roles: list[str] = Field(default_factory=lambda: ["member"], max_length=8)
    token_limit: int | None = Field(default=None, ge=0)


class AdminUpdateUserRequest(BaseModel):
    """The administrator-writable fields of one account."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)
    status: str | None = Field(default=None, max_length=20)
    platform_roles: list[str] | None = Field(default=None, max_length=8)


class UserPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[UserProfileView]
    total: int


def profile_view(profile: UserProfile) -> UserProfileView:
    return UserProfileView(
        user_id=profile.id,
        email=profile.email,
        display_name=profile.display_name,
        status=profile.status,
        platform_roles=sorted(profile.platform_roles),
        has_avatar=profile.avatar_content is not None,
        avatar_content_type=profile.avatar_content_type,
        avatar_updated_at=profile.avatar_updated_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
