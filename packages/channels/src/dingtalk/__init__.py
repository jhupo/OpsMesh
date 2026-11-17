"""DingTalk robot channel with official Stream SDK connections."""

from __future__ import annotations

from .adapter import DingTalkAdapter
from .channel import DingTalkChannel
from .config import (
    DingTalkConnectionSettings,
    DingTalkSettings,
)
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
    "parse_callback",
]
