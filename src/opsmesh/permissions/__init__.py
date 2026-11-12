"""Public permissions with checks loaded after matcher initialization."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .base import USER as USER
from .base import Permission as Permission
from .base import User as User

if TYPE_CHECKING:
    from .checks import MESSAGE as MESSAGE
    from .checks import METAEVENT as METAEVENT
    from .checks import NOTICE as NOTICE
    from .checks import REQUEST as REQUEST
    from .checks import Message as Message
    from .checks import MetaEvent as MetaEvent
    from .checks import Notice as Notice
    from .checks import Request as Request

_CHECKS = (
    "MESSAGE",
    "METAEVENT",
    "NOTICE",
    "REQUEST",
    "Message",
    "MetaEvent",
    "Notice",
    "Request",
)


__all__ = ("Permission", "User", "USER", *_CHECKS)


def __getattr__(name: str) -> Any:
    """Defer checks that depend on matcher injection descriptors."""
    if name not in _CHECKS:
        raise AttributeError(f"Unknown permissions export: {name}")
    value = getattr(import_module(".checks", __name__), name)
    globals()[name] = value
    return value
