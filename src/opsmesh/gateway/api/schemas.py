"""Management inputs; credentials and reply addresses are write-only."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh.gateway.channels.policy import GatewayPolicy


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    enabled: bool = False
    policy: GatewayPolicy = Field(default_factory=GatewayPolicy)


class ChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, JsonValue] | None = Field(default=None, repr=False)
    enabled: bool | None = None
    policy: GatewayPolicy | None = None


class AccountBind(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class RouteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_id: UUID | None = None
    conversation_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    user_id: UUID | None = None
    conversation_id: str = Field(default="", max_length=500)
    target_kind: str = Field(pattern="^(agent|workflow)$")
    target_id: UUID
    allow_group: bool = False
    output_mode: Literal["text", "message"] = "text"
    enabled: bool = True


class BindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
