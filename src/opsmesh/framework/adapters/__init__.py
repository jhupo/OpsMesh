"""Public bot protocol abstractions."""

from __future__ import annotations

from .adapter import OpsMeshAdapter
from .bot import BotRef, OpsMeshBot, ReplyTarget
from .configuration import (
    BotAdapterConnectionStatus,
    BotAdapterDescriptor,
    BotConnectionConfiguration,
)
from .event import MessageEvent, OpsMeshEvent, TimerEvent
from .ingress import (
    BotInboundEvent,
    BotMessageEvent,
    BotWebhookRequest,
    BotWebhookResponse,
    BotWebhookResult,
)
from .managed import BotEventConsumer, OpsMeshBotAdapter
from .message import OpsMeshMessage, OpsMeshMessageSegment
from .registry import BotAdapterRegistry

__all__ = [
    "BotAdapterConnectionStatus",
    "BotAdapterDescriptor",
    "BotAdapterRegistry",
    "BotConnectionConfiguration",
    "BotEventConsumer",
    "BotInboundEvent",
    "BotMessageEvent",
    "BotRef",
    "BotWebhookRequest",
    "BotWebhookResponse",
    "BotWebhookResult",
    "MessageEvent",
    "OpsMeshAdapter",
    "OpsMeshBot",
    "OpsMeshBotAdapter",
    "OpsMeshEvent",
    "OpsMeshMessage",
    "OpsMeshMessageSegment",
    "ReplyTarget",
    "TimerEvent",
]
