"""Declare callbacks on the plugin that owns them."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

if TYPE_CHECKING:
    from opsmesh.drivers import Driver

SYNC_LIFESPAN_FUNC: TypeAlias = Callable[[], Any]
ASYNC_LIFESPAN_FUNC: TypeAlias = Callable[[], Awaitable[Any]]
LIFESPAN_FUNC: TypeAlias = SYNC_LIFESPAN_FUNC | ASYNC_LIFESPAN_FUNC

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True, eq=False)
class LifecycleHook:
    """One callback, optionally receiving its owning Driver at activation."""

    func: Callable[..., Any]
    with_driver: bool = False

    def bind(self, driver: "Driver | None") -> LIFESPAN_FUNC:
        """Bind the explicit Driver argument for this execution."""
        if not self.with_driver:
            return self.func
        if driver is None:
            raise RuntimeError("Lifecycle callback requires a Driver")
        return partial(self.func, driver)


def on_startup(
    func: F | None = None, *, with_driver: bool = False, before: bool = False
):
    """Declare startup work; providers can prepare before consumer startup."""

    def register(callback: F) -> F:
        return _declare(
            "prepare" if before else "startup",
            callback,
            with_driver=with_driver,
        )

    return register if func is None else register(func)


def on_shutdown(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Declare a callback released with its owning plugin."""
    return _declare("shutdown", func)


def on_stopping(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Declare work to drain before any plugin releases its resources."""
    return _declare("stopping", func)


def on_ready(func: F | None = None, *, with_driver: bool = False):
    """Declare readiness work; opt in to receiving the Driver."""

    def register(callback: F) -> F:
        return _declare("ready", callback, with_driver=with_driver)

    return register if func is None else register(func)


def on_started(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Activate inputs after every plugin's readiness callbacks finish."""
    return _declare("started", func)


def on_quiescing(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Close and drain external inputs before stopping their consumers."""
    return _declare("quiescing", func)


def _declare(phase: str, func: F, *, with_driver: bool = False) -> F:
    from opsmesh.plugins.model import PluginStatus
    from opsmesh.plugins.registry import (
        _current_plugin,
        track_registration,
    )

    if not callable(func):
        raise TypeError("Lifecycle callbacks must be callable")
    plugin = _current_plugin.get()
    if plugin is None:
        return func
    if plugin.status is not PluginStatus.LOADED:
        raise RuntimeError("Lifecycle callbacks must be declared while loading")
    entries = plugin.lifecycle_hooks.setdefault(phase, [])
    entry = LifecycleHook(func, with_driver)
    entries.append(entry)
    track_registration(lambda: entries.remove(entry))
    return func
