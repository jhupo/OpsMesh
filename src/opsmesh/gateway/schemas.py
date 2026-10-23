"""Management inputs; credentials and reply addresses are write-only."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    enabled: bool = False


class ChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, JsonValue] | None = Field(default=None, repr=False)
    enabled: bool | None = None


class AccountBind(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class RouteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_id: UUID
    conversation_id: str = Field(default="", max_length=500)
    target_kind: str = Field(pattern="^(agent|workflow)$")
    target_id: UUID
    allow_group: bool = False
    enabled: bool = True


class DeliveryResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["retry", "acknowledge"]
    accept_duplicate_risk: bool = False
    remote_id: str = Field(default="", max_length=500)
