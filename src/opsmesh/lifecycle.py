"""Coordinate lifecycle callbacks and their shared background task group."""

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, TypeAlias, cast

import anyio
from anyio.abc import TaskGroup

from opsmesh.utils import is_coroutine_callable, run_sync

if TYPE_CHECKING:
    from opsmesh.plugins.model import Plugin

logger = logging.getLogger(__name__)

SYNC_LIFESPAN_FUNC: TypeAlias = Callable[[], Any]
ASYNC_LIFESPAN_FUNC: TypeAlias = Callable[[], Awaitable[Any]]
LIFESPAN_FUNC: TypeAlias = SYNC_LIFESPAN_FUNC | ASYNC_LIFESPAN_FUNC

_declarations: dict[tuple[str, str, str], LIFESPAN_FUNC] = {}


def on_startup(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Declare a callback activated when its plugin is loaded."""
    return _declare("startup", func)


def on_shutdown(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Declare a callback released with its owning plugin."""
    return _declare("shutdown", func)


def on_stopping(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Declare work to drain before any plugin releases its resources."""
    return _declare("stopping", func)


def on_ready(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Declare a callback for the plugin readiness phase."""
    return _declare("ready", func)


def on_started(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Activate inputs after every plugin's readiness callbacks finish."""
    return _declare("started", func)


def on_quiescing(func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    """Close and drain external inputs before stopping their consumers."""
    return _declare("quiescing", func)


def _declare(phase: str, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
    from opsmesh.plugins.model import PluginStatus
    from opsmesh.plugins.registry import get_plugin_by_module_name

    if not callable(func):
        raise TypeError("Lifecycle callbacks must be callable")
    plugin = get_plugin_by_module_name(func.__module__)
    if plugin is not None and plugin.status is not PluginStatus.LOADED:
        raise RuntimeError("Lifecycle callbacks must be declared while loading")
    _declarations[(func.__module__, func.__qualname__, phase)] = func
    return func


def bind_plugin_lifecycle(plugin: "Plugin") -> None:
    """Bind explicitly declared hooks within the loader's ownership scope.

    Ordinary imports only declare callbacks; they do not acquire a runtime
    or start resources. The plugin loader activates declarations once and
    the existing registration ledger removes them on load failure/revocation.
    """
    from opsmesh.drivers import get_driver
    from opsmesh.plugins.registry import get_plugin_by_module_name

    callbacks = [
        (phase, func)
        for (module, _, phase), func in _declarations.items()
        if get_plugin_by_module_name(module) is plugin
    ]
    if not callbacks:
        return
    driver = get_driver()
    for phase, func in callbacks:
        getattr(driver, f"on_{phase}")(func)


@dataclass(frozen=True)
class _Entry:
    func: LIFESPAN_FUNC
    plugin: "Plugin | None"


class Lifespan:
    """Start plugins, drain admitted work, then release plugin resources.

    Callbacks registered while a plugin is being imported belong to that
    plugin. Their startup order follows the recorded plugin dependency graph,
    and a failed non-required plugin only blocks its dependents. Callbacks
    registered outside plugin loading run in registration order.
    All stopping callbacks finish before any shutdown callback runs;
    each shutdown phase runs in reverse registration order.
    """

    def __init__(self) -> None:
        self._task_group: TaskGroup | None = None

        self._startup_funcs: list[_Entry] = []
        self._ready_funcs: list[_Entry] = []
        self._started_funcs: list[_Entry] = []
        self._quiescing_funcs: list[_Entry] = []
        self._stopping_funcs: list[_Entry] = []
        self._shutdown_funcs: list[_Entry] = []

    @property
    def started(self) -> bool:
        """Whether the task group is owned by this lifecycle."""
        return self._task_group is not None

    @property
    def task_group(self) -> TaskGroup:
        """The active background task group; unavailable before startup."""
        if self._task_group is None:
            raise RuntimeError("Lifespan not started")
        return self._task_group

    @task_group.setter
    def task_group(self, task_group: TaskGroup) -> None:
        if self._task_group is not None:
            raise RuntimeError("Lifespan already started")
        self._task_group = task_group

    @staticmethod
    def _register(
        callbacks: list[_Entry], func: LIFESPAN_FUNC
    ) -> LIFESPAN_FUNC:
        from opsmesh.plugins.registry import (
            _current_plugin,
            track_registration,
        )

        entry = _Entry(func, _current_plugin.get())
        callbacks.append(entry)

        def remove() -> None:
            for index in range(len(callbacks) - 1, -1, -1):
                if callbacks[index] is entry:
                    del callbacks[index]
                    break

        track_registration(remove, func)
        return func

    def on_startup(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback to run before the readiness phase."""
        return self._register(self._startup_funcs, func)

    def on_shutdown(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback to run during shutdown in reverse order."""
        return self._register(self._shutdown_funcs, func)

    def on_stopping(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register work to drain before resource shutdown callbacks run."""
        return self._register(self._stopping_funcs, func)

    def on_ready(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback to run after all startup callbacks finish."""
        return self._register(self._ready_funcs, func)

    def on_started(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register input activation after the global readiness barrier."""
        return self._register(self._started_funcs, func)

    def on_quiescing(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register input shutdown before consumer stopping callbacks."""
        return self._register(self._quiescing_funcs, func)

    @staticmethod
    async def _run_lifespan_func(
        funcs: Iterable[LIFESPAN_FUNC],
    ) -> None:
        for func in funcs:
            if is_coroutine_callable(func):
                await cast(ASYNC_LIFESPAN_FUNC, func)()
            else:
                await run_sync(cast(SYNC_LIFESPAN_FUNC, func))()

    async def _run_phase(self, entries: list[_Entry], *, final: bool) -> None:
        from opsmesh.plugins.model import PluginStatus
        from opsmesh.plugins.registry import (
            get_plugins_in_dependency_order,
        )

        owned: dict[int, list[_Entry]] = defaultdict(list)
        for entry in entries:
            if entry.plugin is not None:
                owned[id(entry.plugin)].append(entry)

        blocked: set[str] = set()
        try:
            plugins = get_plugins_in_dependency_order()
        except RuntimeError:
            if any(entry.plugin is not None for entry in entries):
                raise
            plugins = []

        for plugin in plugins:
            if plugin.status in (PluginStatus.FAILED, PluginStatus.BLOCKED):
                blocked.add(plugin.id_)
                continue
            failing = [
                dependency
                for dependency in plugin.dependencies
                if dependency in blocked
            ]
            if failing:
                plugin.status = PluginStatus.BLOCKED
                plugin.blocked_by = set(failing)
                blocked.add(plugin.id_)
                message = 'Plugin "%s" is blocked by failed dependencies: %s'
                if plugin.required:
                    raise RuntimeError(
                        message % (plugin.id_, ", ".join(failing))
                    )
                logger.error(message, plugin.id_, ", ".join(failing))
                continue
            plugin.blocked_by.clear()
            if not final and plugin.status is PluginStatus.LOADED:
                plugin.status = PluginStatus.STARTING
            await self._run_owned(
                plugin, owned.get(id(plugin), ()), final=final
            )
            if plugin.status is PluginStatus.FAILED:
                blocked.add(plugin.id_)

        await self._run_lifespan_func(
            entry.func for entry in entries if entry.plugin is None
        )

    async def _run_owned(
        self,
        plugin: "Plugin",
        entries: Iterable[_Entry],
        *,
        final: bool,
    ) -> None:
        from opsmesh.plugins.model import PluginStatus

        try:
            await self._run_lifespan_func(entry.func for entry in entries)
        except BaseException as error:
            plugin.status = PluginStatus.FAILED
            plugin.failure = error
            if plugin.required:
                raise
            logger.error(
                'Plugin "%s" failed during startup; dependents are blocked',
                plugin.id_,
                exc_info=(type(error), error, error.__traceback__),
            )
            return
        if final:
            plugin.status = PluginStatus.READY

    async def startup(self) -> None:
        """Enter the task group and invoke startup and readiness callbacks."""
        self.task_group = anyio.create_task_group()
        await self.task_group.__aenter__()
        try:
            await self._run_phase(self._startup_funcs, final=False)
            await self._run_phase(self._ready_funcs, final=True)
            await self._run_phase(self._started_funcs, final=False)
        except BaseException:
            await self.shutdown()
            raise

    async def shutdown(
        self,
        *,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        """Drain work and release plugin resources before closing tasks."""
        from opsmesh.plugins.model import PluginStatus
        from opsmesh.plugins.registry import get_loaded_plugins

        task_group = self.task_group
        errors: list[BaseException] = []
        try:
            # Hooks may release resources even when the host is cancelled.
            with anyio.CancelScope(shield=True):
                for phase in (
                    self._quiescing_funcs,
                    self._stopping_funcs,
                    self._shutdown_funcs,
                ):
                    for entry in reversed(phase):
                        try:
                            await self._run_lifespan_func((entry.func,))
                        except BaseException as error:
                            errors.append(error)
        finally:
            task_group.cancel_scope.cancel()
            try:
                await task_group.__aexit__(exc_type, exc_val, exc_tb)
            except BaseException as error:
                errors.append(error)
            finally:
                self._task_group = None
                for plugin in get_loaded_plugins():
                    plugin.status = PluginStatus.STOPPED
        if errors:
            raise BaseExceptionGroup("Framework lifespan failed", errors)

    async def __aenter__(self) -> None:
        await self.startup()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.shutdown(exc_type=exc_type, exc_val=exc_val, exc_tb=exc_tb)
