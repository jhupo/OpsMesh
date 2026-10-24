"""Deterministic ingress extensions, independent of agents and workflow engines."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh_sdk.channel_management import READ_ACTIONS, ManagementRequest
from opsmesh_sdk.channels import ChannelMedia, InboundMessage


class MessageFilterResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["allow", "drop"]
    reason: str = Field(default="", max_length=100)
    rejection_input: str = Field(default="", max_length=4000)
    operations: list[ManagementRequest] = Field(default_factory=list, max_length=10)
    scores: dict[str, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]] = Field(
        default_factory=dict, max_length=50
    )

    @model_validator(mode="after")
    def validate_rejection(self) -> "MessageFilterResult":
        if self.action == "allow" and self.rejection_input:
            raise ValueError("Allowed messages cannot request a rejection response")
        if self.action == "allow" and self.operations:
            raise ValueError("Allowed messages cannot request moderation effects")
        if any(item.action in READ_ACTIONS for item in self.operations):
            raise ValueError("Moderation decisions contain only management effects")
        if self.rejection_input and not self.rejection_input.strip():
            raise ValueError("Rejection input cannot contain only whitespace")
        return self


MessageFilter = Callable[[InboundMessage, ChannelMedia], Awaitable[MessageFilterResult]]


@dataclass(frozen=True)
class MessageFilterRegistration:
    handler: MessageFilter
    timeout_seconds: float = 15
