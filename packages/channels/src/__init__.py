"""Plugin-owned HTTP, QQ and DingTalk connections over framework dispatch."""

from importlib import import_module
from types import ModuleType

from opsmesh.plugins import PluginMetadata

from . import lifecycle as _lifecycle  # noqa: F401
from .attachments import Attachment, AttachmentCollection, AttachmentError
from .channel import Channel
from .config import ChannelConnections
from .context import UserContext
from .events import MessageEvent
from .messages import (
    Message,
    MessageSegment,
    StreamMessageEnd,
    StreamMessageStart,
    StreamTextDelta,
)
from .operations import (
    OperationError,
    OperationPermissionDenied,
    OperationRateLimited,
    UnsupportedOperation,
)
from .receipts import Delivery, DeliveryReceipt
from .rules import in_group
from .sending import MessageSendError


def __getattr__(name: str) -> ModuleType:
    """Import a protocol only when a consumer requests its interface."""
    if name not in {"api", "qq", "dingtalk"}:
        raise AttributeError(f"Unknown channels attribute: {name}")
    module = import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module


__plugin_meta__ = PluginMetadata(
    name="channels",
    description="HTTP, QQ and DingTalk transports for plugin-owned events",
    usage=(
        "Depend on channels, declare connections and handle framework events."
    ),
    type="channel",
    config=None,
    dependency_config=ChannelConnections,
)

__all__ = [
    "Attachment",
    "AttachmentCollection",
    "AttachmentError",
    "Channel",
    "Delivery",
    "DeliveryReceipt",
    "Message",
    "MessageEvent",
    "MessageSegment",
    "MessageSendError",
    "OperationError",
    "OperationPermissionDenied",
    "OperationRateLimited",
    "StreamMessageEnd",
    "StreamMessageStart",
    "StreamTextDelta",
    "UnsupportedOperation",
    "UserContext",
    "api",
    "dingtalk",
    "in_group",
    "qq",
]
