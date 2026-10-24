"""Management inputs; credentials and reply addresses are write-only."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh.gateway.channels.policy import GatewayPolicy
from opsmesh.gateway.contracts import ChannelTarget


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    enabled: bool = False
    policy: GatewayPolicy = Field(default_factory=GatewayPolicy)
    target: ChannelTarget | None = None


class ChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, JsonValue] | None = Field(default=None, repr=False)
    enabled: bool | None = None
    policy: GatewayPolicy | None = None
    target: ChannelTarget | None = None


class AccountBind(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class BindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
