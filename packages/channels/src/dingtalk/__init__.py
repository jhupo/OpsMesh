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
    DingTalkMessageEvent,
    DingTalkNoticeEvent,
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
    "DingTalkMessageEvent",
    "DingTalkNoticeEvent",
    "DingTalkProtocolError",
    "DingTalkSendError",
    "DingTalkSettings",
    "FAILED",
    "READY",
    "declare_connection",
    "get_adapter",
    "parse_callback",
]
