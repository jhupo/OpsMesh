"""Bind provider-owned callbacks after a plugin declares its metadata."""

import inspect
from collections.abc import Callable

from .model import Plugin
from .registry import get_plugin_by_module_name, track_registration

_declarations: dict[tuple[str, str], Callable[[Plugin], None]] = {}
_bound: list[Callable[[Plugin], None]] = []


def on_plugin_loaded(
    callback: Callable[[Plugin], None],
) -> Callable[[Plugin], None]:
    """Declare a synchronous metadata consumer, without starting resources.

    A provider is activated by the loader. Its callbacks run in the newly
    loaded consumer's ownership scope so registrations roll back with it.
    """
    if inspect.iscoroutinefunction(callback):
        raise TypeError("Plugin load callbacks must be synchronous")
    _declarations[(callback.__module__, callback.__qualname__)] = callback
    return callback


def bind_plugin_hooks(plugin: Plugin) -> None:
    """Activate declarations once and remove them on provider revocation."""
    for callback in _declarations.values():
        if get_plugin_by_module_name(callback.__module__) is plugin:
            _bound.append(callback)
            track_registration(lambda item=callback: _bound.remove(item))


def notify_plugin_loaded(plugin: Plugin) -> None:
    """Let active providers consume metadata before lifecycle binding."""
    for callback in tuple(_bound):
        callback(plugin)
