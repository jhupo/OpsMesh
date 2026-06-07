from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from backend.app.api.schemas.common import ORMModel, TimestampedModel


class CurrentUserResponse(ORMModel):
    user_id: UUID
    email: str
    display_name: str


class UserRegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=4096)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        candidate = value.strip().lower()
        if "@" not in candidate:
            raise ValueError("Email must contain @")
        return candidate

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("Display name is required")
        return candidate


class UserLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=4096)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        candidate = value.strip().lower()
        if "@" not in candidate:
            raise ValueError("Email must contain @")
        return candidate


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=4096)
    new_password: str = Field(min_length=8, max_length=4096)


class UserAPITokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_at: datetime | None = None


class UserAPITokenResponse(TimestampedModel):
    user_id: UUID
    name: str
    fingerprint: str
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class UserAPITokenCreateResponse(UserAPITokenResponse):
    token: str


class UserAPITokenRevokeAllResponse(BaseModel):
    revoked: int
