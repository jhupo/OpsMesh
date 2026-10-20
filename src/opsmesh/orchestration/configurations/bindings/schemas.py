"""User capability bindings contain parameters and fixed public resource references."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BindingConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters: dict[str, Any] = Field(default_factory=dict)
    instance_id: UUID | None = None
    export_id: str | None = Field(default=None, min_length=1, max_length=100)
    mcp_connection_id: UUID | None = None


class BindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern="^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    version_id: UUID
    configuration: BindingConfiguration = Field(default_factory=BindingConfiguration)


class BindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    version_id: UUID
    status: Literal["enabled", "disabled"] = "enabled"
    configuration: BindingConfiguration = Field(default_factory=BindingConfiguration)


class BindingView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_user_id: UUID
    name: str
    version_id: UUID
    version_digest: str
    revision: int
    status: str
    configuration: BindingConfiguration


class BindingConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    configuration_id: UUID | None = None
    environment_id: UUID | None = None
    transport: Literal["streamable_http", "sse", "stdio"] = "streamable_http"
