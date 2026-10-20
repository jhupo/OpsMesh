"""Public model contracts deliberately exclude encrypted and plaintext keys."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ModelConnectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    api_key: SecretStr = Field(min_length=1, max_length=16384)
    base_url: str = Field(default="https://api.openai.com/v1", max_length=500)
    default_model: str = Field(min_length=1, max_length=200)
    models: list[str] = Field(min_length=1, max_length=256)


class ModelConnectionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=16384)
    base_url: str | None = Field(default=None, max_length=500)
    default_model: str | None = Field(default=None, min_length=1, max_length=200)
    models: list[str] | None = Field(default=None, min_length=1, max_length=256)


class ModelConnectionView(BaseModel):
    connection_id: UUID
    revision: int
    name: str
    provider: str
    base_url: str
    default_model: str
    models: list[str]
    status: str
    last_probe_status: str
    last_probe_detail: str | None
    last_probe_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ModelConnectionPage(BaseModel):
    connections: list[ModelConnectionView]
    total: int


class ModelGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(min_length=1, max_length=256)
    is_default: bool = False


class ModelGrantView(BaseModel):
    connection_id: UUID
    user_id: UUID
    models: list[str]
    is_default: bool


class AvailableModelConnectionView(BaseModel):
    """The user's selectable model subset; no upstream address or secret."""

    connection_id: UUID
    revision: int
    name: str
    provider: str
    models: list[str]
    default_model: str
    is_default: bool
