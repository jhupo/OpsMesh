"""Gateway values, independent of transport and execution engines."""

from typing import Literal
from uuid import UUID

from opsmesh_sdk.channels import ContentPart, InboundMessage
from opsmesh_sdk.identity import InvocationSource
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from opsmesh.gateway.channels.policy import GatewayPolicy
from opsmesh.shared.serialization import payload_digest


class ChannelDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    owner_id: UUID
    kind: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    policy: GatewayPolicy = Field(default_factory=GatewayPolicy)
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
    output_mode: Literal["text", "message"] = "text"
    enabled: bool = True
    revision: int = 1

    @model_validator(mode="after")
    def validate_output(self) -> "RouteDefinition":
        if self.output_mode == "message" and self.target_kind != "workflow":
            raise ValueError("Typed gateway replies require a Workflow route")
        return self


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
    policy: GatewayPolicy = Field(default_factory=GatewayPolicy)
    recent_messages: list[InboundMessage] = Field(default_factory=list)
    context_ids: list[UUID] = Field(default_factory=list)
    event_cursor: int = 0

    @property
    def source(self) -> InvocationSource:
        return InvocationSource(
            channel_id=self.channel_id,
            conversation_id=self.message.conversation.external_id,
            identity_id=self.identity_id,
            reply_scope="private" if self.message.conversation.kind == "private" else "group",
            external_role=self.message.actor.external_role,
            external_id=self.message.actor.external_id,
            identity_scope=self.message.actor.scope,
            stream_output=(
                (self.policy.streaming or self.policy.show_thinking)
                and self.route.output_mode == "text"
                and self.route.target_kind == "agent"
            ),
            session_scope=payload_digest(
                {
                    "route": str(self.route.id),
                    "revision": self.route.revision,
                    "identity": str(self.identity_id),
                    "user": str(self.user_id),
                    "conversation": {
                        "kind": self.message.conversation.kind,
                        "id": self.message.conversation.external_id,
                        "scope": self.message.actor.scope,
                    },
                }
            ),
        )


class GatewayReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: str = Field(pattern="^(reply|ignore)$")
    content: list[ContentPart] = Field(default_factory=list, max_length=100)

    @classmethod
    def parse(cls, value: object) -> "GatewayReply":
        result = cls.model_validate(value)
        if (result.action == "reply") != bool(result.content):
            raise ValueError("Reply requires content; ignore must not contain content")
        return result


class GatewayEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int = Field(ge=1)
    kind: Literal["delta", "summary", "tool_start", "tool_end", "status"]
    text: str = Field(default="", max_length=100_000)


class GatewayEvents(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cursor: int = Field(ge=0)
    events: list[GatewayEvent] = Field(default_factory=list, max_length=100)


class DeliveryResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["retry", "acknowledge"]
    accept_duplicate_risk: bool = False
    remote_id: str = Field(default="", max_length=500)


class PushCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    recipients: list[UUID] = Field(default_factory=list, max_length=100)
    broadcast: bool = False
    content: list[ContentPart] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_recipients(self) -> "PushCreate":
        if bool(self.recipients) == self.broadcast:
            raise ValueError("Select explicit recipients or broadcast to known subjects")
        return self
