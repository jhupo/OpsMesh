"""Import-time plugin registration without a competing setup/exports protocol."""

from __future__ import annotations

from .load import get_plugin_config, load_plugin, load_plugins, require
from .manager import PluginManager, get_current_plugin, manager
from .model import OpsMeshPlugin, OpsMeshPluginMetadata, PluginState
from .on import on, on_command, on_event, on_message, on_regex

__all__ = [
    "OpsMeshPlugin",
    "OpsMeshPluginMetadata",
    "PluginManager",
    "PluginState",
    "get_current_plugin",
    "get_plugin_config",
    "load_plugin",
    "load_plugins",
    "manager",
    "on",
    "on_command",
    "on_event",
    "on_message",
    "on_regex",
    "require",
]
