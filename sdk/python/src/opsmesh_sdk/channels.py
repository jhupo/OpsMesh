"""Transport contracts for system channel plugins, with no server dependency."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChannelValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExternalActor(ChannelValue):
    scope: str = Field(min_length=1, max_length=300)
    id_kind: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=500)
    name: str = ""
    external_role: str | None = None


class Conversation(ChannelValue):
    external_id: str = Field(min_length=1, max_length=500)
    kind: Literal["private", "group", "guild"]
    name: str = ""


class ContentPart(ChannelValue):
    type: Literal["text", "image", "audio", "video", "file"] = "text"
    text: str = ""
    media_key: str | None = None
    mime_type: str = "application/octet-stream"
    filename: str = "attachment"


class InboundMessage(ChannelValue):
    event_id: str = Field(min_length=1, max_length=500)
    actor: ExternalActor
    conversation: Conversation
    content: list[ContentPart] = Field(min_length=1, max_length=100)
    reply_address: dict[str, Any] = Field(default_factory=dict, repr=False)


class OutboundMessage(ChannelValue):
    delivery_id: UUID
    address: dict[str, Any] = Field(repr=False)
    content: list[ContentPart] = Field(min_length=1, max_length=100)


class DeliveryReceipt(ChannelValue):
    remote_id: str = ""


class ChannelCapabilities(ChannelValue):
    inbound_media: bool = False
    outbound_media: bool = False
    member_lookup: bool = False
    streaming: bool = False


EventSink = Callable[[InboundMessage], Awaitable[None]]


class ChannelMedia(Protocol):
    async def save_bytes(self, data: bytes) -> str: ...
    async def read(self, key: str) -> bytes: ...
    async def fetch(self, url: str) -> tuple[str, str]: ...


class ChannelAdapter(ABC):
    """Constructors and validation must not connect. run() owns the connection."""

    capabilities = ChannelCapabilities()

    def __init__(self, config: dict[str, Any], sink: EventSink, media: ChannelMedia) -> None:
        self.config, self.sink, self.media = config, sink, media

    @classmethod
    @abstractmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def run(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def send(self, message: OutboundMessage) -> DeliveryReceipt: ...


class DeliveryRejected(Exception):
    """The platform conclusively rejected a delivery; no message was sent."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class DeliveryUncertain(Exception):
    """The platform may have accepted the message. Do not blindly retry."""
