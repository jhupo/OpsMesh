"""DingTalk robot channel with signed webhook callbacks."""

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
    DingTalkSignatureError,
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
    "DingTalkSignatureError",
    "FAILED",
    "READY",
    "declare_connection",
    "get_adapter",
    "outgoing_text",
    "parse_callback",
]
