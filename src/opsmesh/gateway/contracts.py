"""Gateway values, independent of transport and execution engines."""

from uuid import UUID

from opsmesh_sdk.channels import InboundMessage
from opsmesh_sdk.identity import InvocationSource
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ChannelDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    owner_id: UUID
    kind: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    revision: int = 1


class RouteDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    channel_id: UUID
    identity_id: UUID
    conversation_id: str = Field(default="", max_length=500)
    target_kind: str = Field(pattern="^(agent|workflow)$")
    target_id: UUID
    allow_group: bool = False
    enabled: bool = True
    revision: int = 1


class GatewayTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    inbox_id: UUID
    lease_owner: str
    channel_id: UUID
    identity_id: UUID
    user_id: UUID
    route: RouteDefinition
    message: InboundMessage
    session_id: UUID | None = None

    @property
    def source(self) -> InvocationSource:
        return InvocationSource(
            channel_id=self.channel_id,
            conversation_id=self.message.conversation.external_id,
            identity_id=self.identity_id,
            reply_scope="private" if self.message.conversation.kind == "private" else "group",
        )
