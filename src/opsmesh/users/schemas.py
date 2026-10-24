"""User, group and access token HTTP contracts."""

from datetime import datetime

from pydantic import Field, SecretStr, field_validator

from opsmesh.shared.schemas import RecordView, RequestSchema, ResourceView


class SaveUserRequest(RequestSchema):
    name: str = Field(min_length=1, max_length=100)
    roles: list[str] = Field(default_factory=lambda: ["user"], max_length=8)
    enabled: bool = True
    password: SecretStr | None = Field(default=None, min_length=12, max_length=1000)

    @field_validator("roles")
    @classmethod
    def valid_roles(cls, value: list[str]) -> list[str]:
        if not set(value) <= {"user", "admin"}:
            raise ValueError("Unknown platform role")
        return sorted(set(value))


class CreateGroupRequest(RequestSchema):
    name: str = Field(min_length=1, max_length=100)


class CreateAccessTokenRequest(RequestSchema):
    label: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.utcoffset() is None or value <= datetime.now(value.tzinfo)):
            raise ValueError("Expiry must be a future time with timezone")
        return value


class UserIdentityView(ResourceView):
    user_id: str
    roles: list[str]
    group_ids: list[str]


class UserView(RecordView):
    name: str
    roles: list[str]
    enabled: bool


class GroupView(RecordView):
    name: str


class AccessTokenView(RecordView):
    user_id: str
    label: str
    expires_at: datetime | None
    revoked: bool


class IssuedAccessTokenView(ResourceView):
    token: str


class LoginRequest(RequestSchema):
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=1000)


class ChangePasswordRequest(RequestSchema):
    password: SecretStr = Field(min_length=12, max_length=1000)
