"""Model provider connection HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh.workspaces.model_providers.models import (
    MAX_BASE_URL_LENGTH,
    MAX_MODEL_LENGTH,
    MAX_MODELS,
    MAX_NAME_LENGTH,
)

ProviderName = Literal["openai"]


class ModelProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    credential_id: UUID
    base_url: str | None = Field(default=None, max_length=MAX_BASE_URL_LENGTH)
    default_model: str = Field(min_length=1, max_length=MAX_MODEL_LENGTH)
    models: list[str] = Field(default_factory=list, max_length=MAX_MODELS)
    is_default: bool = False

    @model_validator(mode="after")
    def declare_the_default_model(self) -> ModelProviderCreateRequest:
        if self.default_model not in self.models:
            raise ValueError("The default model must be one of the declared models")
        return self


class ModelProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    base_url: str | None = Field(default=None, max_length=MAX_BASE_URL_LENGTH)
    default_model: str | None = Field(default=None, min_length=1, max_length=MAX_MODEL_LENGTH)
    models: list[str] | None = Field(default=None, max_length=MAX_MODELS)
    is_default: bool | None = None


class ModelProviderView(BaseModel):
    connection_id: UUID
    workspace_id: UUID
    credential_id: UUID
    name: str
    provider: str
    base_url: str | None
    default_model: str
    models: list[str]
    is_default: bool
    status: str
    last_probe_status: str
    last_probe_detail: str | None
    last_probe_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ModelProviderPage(BaseModel):
    connections: list[ModelProviderView]
    total: int


class ProbeView(BaseModel):
    connection_id: UUID
    status: str
    detail: str | None
    probed_at: datetime | None


class DefaultModelView(BaseModel):
    """The resolved default model of one workspace; no secret is part of it."""

    connection_id: UUID
    provider: str
    default_model: str
    base_url: str | None
