"""DingTalk robot channel with official Stream SDK connections."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapter import DingTalkAdapter
    from .channel import DingTalkChannel
    from .config import DingTalkCardSettings, DingTalkConnectionSettings
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

_EXPORTS: dict[str, str] = {
    "DingTalkAdapter": ".adapter",
    "DingTalkChannel": ".channel",
    "DingTalkCardSettings": ".config",
    "DingTalkConnectionSettings": ".config",
    "DingTalkConfigurationError": ".errors",
    "DingTalkError": ".errors",
    "DingTalkProtocolError": ".errors",
    "DingTalkSendError": ".errors",
    "DingTalkMessageEvent": ".events",
    "DingTalkNoticeEvent": ".events",
    "parse_callback": ".events",
}


def __getattr__(name: str) -> Any:
    """Import the SDK-backed protocol only when a consumer requests it."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"Unknown DingTalk channel attribute: {name}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


__all__ = [
    "DingTalkAdapter",
    "DingTalkCardSettings",
    "DingTalkChannel",
    "DingTalkConfigurationError",
    "DingTalkConnectionSettings",
    "DingTalkError",
    "DingTalkMessageEvent",
    "DingTalkNoticeEvent",
    "DingTalkProtocolError",
    "DingTalkSendError",
    "parse_callback",
]
