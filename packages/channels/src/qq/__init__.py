"""QQ official bot channel with verified webhook callbacks."""

from __future__ import annotations

from .adapter import QQAdapter
from .channel import (
    CLOSED,
    CONNECTING,
    DISCONNECTED,
    FAILED,
    READY,
    QQChannel,
    outgoing_text,
)
from .config import QQConnectionSettings, QQSettings
from .connections import declare_connection, get_adapter
from .errors import (
    QQConfigurationError,
    QQError,
    QQProtocolError,
    QQSendError,
    QQSignatureError,
)
from .events import (
    C2C_MESSAGE,
    GROUP_MESSAGE,
    QQMessage,
    QQMessageEvent,
    QQMessageSegment,
    parse_callback,
)

__all__ = [
    "C2C_MESSAGE",
    "CLOSED",
    "CONNECTING",
    "DISCONNECTED",
    "FAILED",
    "GROUP_MESSAGE",
    "QQAdapter",
    "QQChannel",
    "QQConfigurationError",
    "QQConnectionSettings",
    "QQError",
    "QQMessage",
    "QQMessageEvent",
    "QQMessageSegment",
    "QQProtocolError",
    "QQSendError",
    "QQSettings",
    "QQSignatureError",
    "READY",
    "declare_connection",
    "get_adapter",
    "outgoing_text",
    "parse_callback",
]
