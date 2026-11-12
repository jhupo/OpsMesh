"""Plugin-owned HTTP and bot entrances over framework event dispatch."""

from importlib import import_module
from types import ModuleType

from opsmesh.plugins import PluginMetadata

from .config import ChannelsSettings
from .context import UserContext
from .dependencies import Connection
from .lifecycle import hooks as _lifecycle  # noqa: F401


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
        "Depend on channels, declare entrances and handle framework events."
    ),
    type="channel",
    config=ChannelsSettings,
)

__all__ = [
    "ChannelsSettings",
    "Connection",
    "UserContext",
    "api",
    "dingtalk",
    "qq",
]
