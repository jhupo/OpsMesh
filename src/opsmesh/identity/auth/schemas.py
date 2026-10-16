"""Login, password and session HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from opsmesh.identity.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=320)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    email: str
    display_name: str
    status: str
    platform_roles: list[str]


class SessionGrant(BaseModel):
    """A session grant returned by registration and login."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    expires_at: datetime
    user: AuthenticatedUser


class SessionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    expires_at: datetime
    revoked_at: datetime | None
