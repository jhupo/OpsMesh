"""Independent OneBot v11 compatibility adapter for HTTP and WebSocket deployments."""

from opsmesh.infrastructure.bots.onebot.adapter import OneBotAdapter
from opsmesh.infrastructure.bots.onebot.bot import OneBot
from opsmesh.infrastructure.bots.onebot.configuration import OneBotSettings
from opsmesh.infrastructure.bots.onebot.event import OneBotMessageEvent, decode_event

__all__ = ["OneBot", "OneBotAdapter", "OneBotMessageEvent", "OneBotSettings", "decode_event"]
