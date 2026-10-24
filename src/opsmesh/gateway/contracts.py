"""Gateway values, independent of transport and execution engines."""

from typing import Literal
from uuid import UUID

from opsmesh_sdk.channels import ContentPart, InboundMessage
from opsmesh_sdk.identity import InvocationSource, MessageParticipant
from opsmesh_sdk.messages import MessageFilterResult
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from opsmesh.gateway.channels.policy import GatewayPolicy
from opsmesh.shared.serialization import payload_digest


class ChannelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: UUID
    target_kind: Literal["agent", "workflow"]
    target_id: UUID
    output_mode: Literal["text", "message"] = "text"


class ChannelDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    owner_id: UUID
    kind: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    policy: GatewayPolicy = Field(default_factory=GatewayPolicy)
    target: ChannelTarget | None = None
    binding_revision: int = Field(default=1, ge=1)
    revision: int = 1

    @model_validator(mode="after")
    def validate_target(self) -> "ChannelDefinition":
        if self.enabled and self.target is None:
            raise ValueError("Enabled channels require exactly one Agent or Workflow target")
        return self


class BatchMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    inbox_id: UUID
    identity_id: UUID
    message: InboundMessage


class GatewayTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    inbox_id: UUID
    lease_owner: str
    channel_id: UUID
    identity_id: UUID
    user_id: UUID
    target: ChannelTarget
    binding_revision: int = Field(default=1, ge=1)
    message: InboundMessage
    session_id: UUID | None = None
    policy: GatewayPolicy = Field(default_factory=GatewayPolicy)
    batch: list[BatchMessage] = Field(default_factory=list)
    event_cursor: int = 0
    status: str = "pending"
    admission: MessageFilterResult | None = None
    channel_revision: int = 1
    queue_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @property
    def source(self) -> InvocationSource:
        mixed = len({item.identity_id for item in self.batch}) > 1
        return InvocationSource(
            channel_id=self.channel_id,
            receipt_id=self.inbox_id,
            conversation_id=self.message.conversation.external_id,
            identity_id=None if mixed else self.identity_id,
            reply_scope="private" if self.message.conversation.kind == "private" else "group",
            external_role=None if mixed else self.message.actor.external_role,
            external_id=None if mixed else self.message.actor.external_id,
            identity_scope=self.message.actor.scope,
            stream_output=(
                (self.policy.streaming or self.policy.show_thinking)
                and self.target.output_mode == "text"
                and self.target.target_kind == "agent"
            ),
            session_scope=self.queue_key
            or payload_digest(
                {
                    "channel": str(self.channel_id),
                    "target": [self.target.target_kind, str(self.target.target_id)],
                    "identity": str(self.identity_id)
                    if self.message.conversation.kind == "private"
                    else None,
                    "user": str(self.user_id),
                    "conversation": {
                        "kind": self.message.conversation.kind,
                        "id": self.message.conversation.external_id,
                        "scope": self.message.actor.scope,
                    },
                }
            ),
            participants=[
                MessageParticipant(
                    message_id=item.message.event_id,
                    identity_id=item.identity_id,
                    external_id=item.message.actor.external_id,
                    external_role=item.message.actor.external_role,
                )
                for item in self.batch
            ],
        )


class GatewayReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["reply", "ignore"]
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
