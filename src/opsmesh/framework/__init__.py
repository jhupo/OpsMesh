"""Public event-driven plugin framework. No platform business dependencies."""

from __future__ import annotations

from .drivers import DriverState, OpsMeshDriver
from .exception import ContextUnavailableError
from .plugin import (
    OpsMeshPluginMetadata,
    get_plugin_config,
    load_plugin,
    load_plugins,
    on,
    on_command,
    on_event,
    on_message,
    on_regex,
    require,
)

_driver: OpsMeshDriver | None = None


def init(driver: OpsMeshDriver | None = None) -> OpsMeshDriver:
    """Initialize the process driver; call before importing plugins."""
    global _driver
    if _driver is not None and _driver.state not in (DriverState.NEW, DriverState.STOPPED):
        raise RuntimeError("An application driver is already running")
    _driver = driver if driver is not None else OpsMeshDriver()
    return _driver


def get_driver() -> OpsMeshDriver:
    if _driver is None:
        raise ContextUnavailableError("Call opsmesh.framework.init() before loading plugins")
    return _driver


__all__ = [
    "OpsMeshPluginMetadata",
    "get_driver",
    "get_plugin_config",
    "init",
    "load_plugin",
    "load_plugins",
    "on",
    "on_command",
    "on_event",
    "on_message",
    "on_regex",
    "require",
]
