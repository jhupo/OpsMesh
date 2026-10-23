"""Transport contracts for system channel plugins, with no server dependency."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class MessageReference(ChannelValue):
    message_id: str = Field(min_length=1, max_length=500)
    sender_id: str | None = Field(default=None, max_length=500)


class ContentPart(ChannelValue):
    type: Literal["text", "image", "audio", "video", "file"] = "text"
    text: str = Field(default="", max_length=100_000)
    media_key: str | None = Field(default=None, min_length=1, max_length=500)
    url: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    data: str | None = Field(default=None, min_length=1, max_length=10_666_668, repr=False)
    mime_type: str = "application/octet-stream"
    filename: str = "attachment"

    @model_validator(mode="after")
    def validate_source(self) -> "ContentPart":
        sources = sum(value is not None for value in (self.media_key, self.url, self.data))
        if (self.type == "text" and sources) or (self.type != "text" and sources != 1):
            raise ValueError("Media requires exactly one key, public URL or base64 source")
        return self


class ContextMessage(ChannelValue):
    message_id: str = Field(min_length=1, max_length=500)
    sender_id: str = Field(min_length=1, max_length=500)
    sender_name: str = Field(default="", max_length=500)
    timestamp: float = Field(ge=0, allow_inf_nan=False)
    content: list[ContentPart] = Field(min_length=1, max_length=100)


class MessageContext(ChannelValue):
    bot_mentioned: bool = False
    mentions: list[str] = Field(default_factory=list, max_length=100)
    reference: MessageReference | None = None
    event_type: str = Field(default="", max_length=100)
    visibility: Literal["unknown", "all", "mention_recent", "mention_only"] = "unknown"
    recent_messages: list[ContextMessage] = Field(default_factory=list, max_length=100)


class InboundMessage(ChannelValue):
    event_id: str = Field(min_length=1, max_length=500)
    actor: ExternalActor
    conversation: Conversation
    content: list[ContentPart] = Field(min_length=1, max_length=100)
    context: MessageContext = Field(default_factory=MessageContext)
    reply_address: dict[str, Any] = Field(default_factory=dict, repr=False)


class OutboundMessage(ChannelValue):
    delivery_id: UUID
    address: dict[str, Any] = Field(repr=False)
    content: list[ContentPart] = Field(max_length=100)
    event: Literal["message", "delta", "completed", "typing", "recall"] = "message"
    stream_id: str | None = None
    stream_index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_content(self) -> "OutboundMessage":
        if (self.event == "recall") == bool(self.content):
            raise ValueError("Recall requires empty content; other events require content")
        return self


class DeliveryReceipt(ChannelValue):
    remote_id: str = ""


class ChannelCapabilities(ChannelValue):
    inbound_media: bool = False
    outbound_media: bool = False
    member_lookup: bool = False
    streaming: bool = False
    typing: bool = False
    proactive: bool = False
    qr_binding: bool = False
    probe: bool = False
    recall: bool = False


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

    async def probe(self) -> dict[str, Any]:
        raise DeliveryRejected("This channel does not support a connection probe")


class DeliveryRejected(Exception):
    """The platform conclusively rejected a delivery; no message was sent."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class DeliveryUncertain(Exception):
    """The platform may have accepted the message. Do not blindly retry."""
