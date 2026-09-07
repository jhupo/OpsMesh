from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

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


class UserAPITokenScopes(BaseModel):
    workspace_ids: list[UUID] = Field(default_factory=list, max_length=100)
    workspace_actions: list[
        Literal[
            "read",
            "write",
            "approve",
            "operate",
            "manage_runtime",
            "manage_capability",
            "manage_members",
            "admin",
            "owner",
        ]
    ] = Field(default_factory=list, max_length=9)
    account_actions: list[
        Literal[
            "profile:read",
            "password:change",
            "tokens:read",
            "tokens:manage",
            "workspaces:create",
        ]
    ] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _validate_workspace_scope(self) -> "UserAPITokenScopes":
        if bool(self.workspace_ids) != bool(self.workspace_actions):
            raise ValueError("workspace_ids and workspace_actions must be granted together")
        if len(set(self.workspace_ids)) != len(self.workspace_ids):
            raise ValueError("workspace_ids must not contain duplicates")
        if len(set(self.workspace_actions)) != len(self.workspace_actions):
            raise ValueError("workspace_actions must not contain duplicates")
        if len(set(self.account_actions)) != len(self.account_actions):
            raise ValueError("account_actions must not contain duplicates")
        return self


class UserAPITokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_at: datetime | None = None
    scopes: UserAPITokenScopes | None = None


class UserAPITokenResponse(TimestampedModel):
    user_id: UUID
    name: str
    fingerprint: str
    status: str
    scopes: UserAPITokenScopes | None
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class UserAPITokenCreateResponse(UserAPITokenResponse):
    token: str


class UserAPITokenRevokeAllResponse(BaseModel):
    revoked: int
