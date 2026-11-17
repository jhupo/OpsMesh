"""QQ Bot API v2 channel with an official Gateway WebSocket."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapter import QQAdapter
    from .channel import QQChannel
    from .config import QQConnectionSettings
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

_EXPORTS: dict[str, str] = {
    "QQAdapter": ".adapter",
    "QQChannel": ".channel",
    "QQConnectionSettings": ".config",
    "QQConfigurationError": ".errors",
    "QQError": ".errors",
    "QQProtocolError": ".errors",
    "QQSendError": ".errors",
    "QQGroupMuteChange": ".operations",
    "QQOperations": ".operations",
    "QQMessageEvent": ".events",
    "QQNoticeEvent": ".events",
    "C2C_MESSAGE": ".events",
    "GROUP_MESSAGE": ".events",
    "GROUP_MESSAGE_CREATE": ".events",
    "GROUP_JOIN_REQUEST": ".events",
    "parse_callback": ".events",
}


def __getattr__(name: str) -> Any:
    """Import the SDK-backed protocol only when a consumer requests it."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"Unknown QQ channel attribute: {name}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


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
    "QQMessageEvent",
    "QQNoticeEvent",
    "QQOperations",
    "QQProtocolError",
    "QQSendError",
    "parse_callback",
]
