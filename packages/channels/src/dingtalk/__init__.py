"""DingTalk robot channel with official Stream SDK connections."""

from __future__ import annotations

from .adapter import DingTalkAdapter
from .channel import (
    CLOSED,
    CONNECTING,
    DISCONNECTED,
    FAILED,
    READY,
    DingTalkChannel,
    outgoing_text,
)
from .config import (
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from .connections import declare_connection, get_adapter
from .errors import (
    DingTalkConfigurationError,
    DingTalkError,
    DingTalkProtocolError,
    DingTalkSendError,
)
from .events import (
    DingTalkMessage,
    DingTalkMessageEvent,
    DingTalkMessageSegment,
    parse_callback,
)

__all__ = [
    "CLOSED",
    "CONNECTING",
    "DISCONNECTED",
    "DingTalkAdapter",
    "DingTalkChannel",
    "DingTalkConfigurationError",
    "DingTalkConnectionSettings",
    "DingTalkError",
    "DingTalkMessage",
    "DingTalkMessageEvent",
    "DingTalkMessageSegment",
    "DingTalkProtocolError",
    "DingTalkSendError",
    "DingTalkSettings",
    "FAILED",
    "READY",
    "declare_connection",
    "get_adapter",
    "outgoing_text",
    "parse_callback",
]
