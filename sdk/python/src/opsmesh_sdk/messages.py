"""Deterministic ingress extensions, independent of agents and workflow engines."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from opsmesh_sdk.channels import ChannelMedia, InboundMessage


class MessageFilterResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["allow", "drop", "recall"]
    reason: str = Field(default="", max_length=100)
    scores: dict[str, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]] = Field(
        default_factory=dict, max_length=50
    )


MessageFilter = Callable[[InboundMessage, ChannelMedia], Awaitable[MessageFilterResult]]


@dataclass(frozen=True)
class MessageFilterRegistration:
    handler: MessageFilter
    timeout_seconds: float = 15
