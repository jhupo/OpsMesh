"""QQ Bot API v2 channel with an official Gateway WebSocket."""

from __future__ import annotations

from .adapter import QQAdapter
from .channel import QQChannel
from .config import QQConnectionSettings, QQSettings
from .errors import (
    QQConfigurationError,
    QQError,
    QQProtocolError,
    QQSendError,
)
from .events import (
    C2C_MESSAGE,
    GROUP_JOIN_REQUEST,
    GROUP_MESSAGE,
    GROUP_MESSAGE_CREATE,
    QQMessageEvent,
    QQNoticeEvent,
    parse_callback,
)
from .operations import QQGroupMuteChange, QQOperations

__all__ = [
    "C2C_MESSAGE",
    "GROUP_MESSAGE",
    "GROUP_MESSAGE_CREATE",
    "GROUP_JOIN_REQUEST",
    "QQAdapter",
    "QQChannel",
    "QQConfigurationError",
    "QQConnectionSettings",
    "QQError",
    "QQGroupMuteChange",
    "QQOperations",
    "QQMessageEvent",
    "QQNoticeEvent",
    "QQProtocolError",
    "QQSendError",
    "QQSettings",
    "parse_callback",
]
