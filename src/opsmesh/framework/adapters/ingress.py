"""Authenticated protocol input, normalized events and protocol acknowledgements."""

from dataclasses import dataclass, field

from opsmesh.framework.typing import JsonData

from .event import MessageEvent, OpsMeshEvent


class BotMessageEvent(MessageEvent):
    """A message with an explicit provider conversation and reply message identity."""

    channel_id: str
    message_id: str


@dataclass(frozen=True)
class BotInboundEvent:
    event: OpsMeshEvent
    payload: JsonData
    ignored: bool = False


@dataclass(frozen=True)
class BotWebhookRequest:
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        return next(
            (value for key, value in self.headers.items() if key.lower() == name.lower()), None
        )


@dataclass(frozen=True)
class BotWebhookResponse:
    body: JsonData = field(default_factory=dict)
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BotWebhookResult:
    incoming: BotInboundEvent | None = None
    response: BotWebhookResponse = field(default_factory=BotWebhookResponse)
