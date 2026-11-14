"""Coordinate lifecycle callbacks and their shared background task group."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
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
_managing: ContextVar[asyncio.Task | None] = ContextVar(
    "plugin_management", default=None
)
_changing: ContextVar[asyncio.Task | None] = ContextVar(
    "plugin_change", default=None
)


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
    from opsmesh.plugins.registry import (
        get_plugin_by_module_name,
        track_registration,
    )

    if not callable(func):
        raise TypeError("Lifecycle callbacks must be callable")
    plugin = get_plugin_by_module_name(func.__module__)
    if plugin is not None and plugin.status is not PluginStatus.LOADED:
        raise RuntimeError("Lifecycle callbacks must be declared while loading")
    key = (func.__module__, func.__qualname__, phase)
    _declarations[key] = func

    def remove() -> None:
        if _declarations.get(key) is func:
            del _declarations[key]

    track_registration(remove, func)
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
        self._management_lock = anyio.Lock()
        self._closing = False
        self._management_scopes: set[anyio.CancelScope] = set()
        self.shutdown_timeout = 30.0

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
            if not plugin.enabled:
                blocked.add(plugin.id_)
                continue
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
                plugin.generation += 1
                plugin.cleanup_complete = False
                plugin.completed_shutdown_hooks.clear()
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
        from opsmesh.plugins.registry import _current_plugin

        token = _current_plugin.set(plugin)
        management = _managing.set(asyncio.current_task())
        try:
            if plugin.metadata and plugin.metadata.config:
                plugin.metadata.config.model_validate(
                    plugin.config_values or {}
                )
            await self._run_lifespan_func(entry.func for entry in entries)
        except BaseException as error:
            plugin.status = PluginStatus.FAILED
            plugin.failure = error
            try:
                await self._close_plugin(plugin)
            except BaseException as cleanup_error:
                plugin.failure = BaseExceptionGroup(
                    "Plugin startup and cleanup failed", [error, cleanup_error]
                )
            if plugin.required or not isinstance(error, Exception):
                raise
            logger.error(
                'Plugin "%s" failed during startup; dependents are blocked',
                plugin.id_,
                exc_info=(type(error), error, error.__traceback__),
            )
            return
        finally:
            _current_plugin.reset(token)
            _managing.reset(management)
        if final:
            plugin.status = PluginStatus.READY

    async def startup(self) -> None:
        """Enter the task group and invoke startup and readiness callbacks."""
        self._closing = False
        self.task_group = anyio.create_task_group()
        await self.task_group.__aenter__()
        try:
            await self._run_phase(self._startup_funcs, final=False)
            await self._run_phase(self._ready_funcs, final=True)
            await self._run_phase(self._started_funcs, final=False)
        except BaseException:
            await self.shutdown()
            raise

    def check_stoppable(self, plugin: "Plugin") -> None:
        """Reject self-stop and active consumers before changing resources."""
        from opsmesh.plugins.errors import PluginProtectedError
        from opsmesh.plugins.model import PluginStatus
        from opsmesh.plugins.registry import get_loaded_plugins

        if plugin.protected:
            raise PluginProtectedError(f"Plugin is protected: {plugin.id_}")

        current = asyncio.current_task()
        if any(current in call.tasks for call in plugin.calls.values()):
            raise RuntimeError(
                "A plugin call cannot stop itself or its provider"
            )
        consumers = [
            item.id_
            for item in get_loaded_plugins()
            if plugin.id_ in item.dependencies
            and (
                item.status
                in (
                    PluginStatus.READY,
                    PluginStatus.STARTING,
                    PluginStatus.STOPPING,
                )
                or not item.cleanup_complete
            )
        ]
        if consumers:
            raise RuntimeError(
                f"Stop dependent plugins first: {', '.join(sorted(consumers))}"
            )

    async def start_plugin(self, plugin: "Plugin") -> None:
        """Start resources for one loaded plugin and its dependencies."""
        await self._manage(self._start_plugin, plugin)

    async def stop_plugin(self, plugin: "Plugin") -> None:
        """Stop one plugin without changing its persisted enabled setting."""
        self.check_stoppable(plugin)
        await self._manage(self._stop_plugin, plugin)

    async def _manage(self, operation, plugin: "Plugin") -> None:
        if _managing.get() is asyncio.current_task():
            raise RuntimeError(
                "Lifecycle callbacks cannot nest management operations"
            )
        if _changing.get() is asyncio.current_task():
            if self._closing:
                raise RuntimeError("The host is shutting down")
            await operation(plugin)
            return
        if not self.started or self._closing:
            raise RuntimeError("The host lifecycle is not running")
        done = anyio.Event()
        errors: list[BaseException] = []

        async def execute() -> None:
            token = _managing.set(asyncio.current_task())
            try:
                with anyio.CancelScope() as scope:
                    self._management_scopes.add(scope)
                    try:
                        async with self._management_lock:
                            if self._closing:
                                raise RuntimeError("The host is shutting down")
                            await operation(plugin)
                    finally:
                        self._management_scopes.discard(scope)
                if scope.cancel_called:
                    raise RuntimeError(
                        "Plugin management was cancelled during shutdown"
                    )
            except BaseException as error:
                errors.append(error)
            finally:
                _managing.reset(token)
                done.set()

        self.task_group.start_soon(execute)
        with anyio.CancelScope(shield=True):
            await done.wait()
        if errors:
            raise errors[0]

    @asynccontextmanager
    async def change(self):
        """Serialize persisted intent and runtime mutation together."""
        if (
            _managing.get() is asyncio.current_task()
            or _changing.get() is asyncio.current_task()
        ):
            raise RuntimeError(
                "Lifecycle callbacks cannot nest management operations"
            )
        if self._closing:
            raise RuntimeError("The host is shutting down")
        async with self._management_lock:
            if self._closing:
                raise RuntimeError("The host is shutting down")
            token = _changing.set(asyncio.current_task())
            try:
                with anyio.CancelScope(shield=True) as scope:
                    self._management_scopes.add(scope)
                    try:
                        yield
                    finally:
                        self._management_scopes.discard(scope)
                if scope.cancel_called:
                    raise RuntimeError(
                        "Plugin management was cancelled during shutdown"
                    )
            finally:
                _changing.reset(token)

    async def _owned_phase(
        self, entries: list[_Entry], plugin: "Plugin"
    ) -> None:
        from opsmesh.plugins.registry import _current_plugin

        token = _current_plugin.set(plugin)
        management = _managing.set(asyncio.current_task())
        try:
            await self._run_lifespan_func(
                entry.func for entry in tuple(entries) if entry.plugin is plugin
            )
        finally:
            _current_plugin.reset(token)
            _managing.reset(management)

    async def _start_plugin(self, plugin: "Plugin") -> None:
        from opsmesh.plugins.model import PluginStatus
        from opsmesh.plugins.registry import (
            get_plugin,
            get_plugins_in_dependency_order,
        )

        if get_plugin(plugin.id_) is not plugin:
            raise RuntimeError(
                "Plugin declarations were revoked before startup"
            )
        get_plugins_in_dependency_order()
        if plugin.ready:
            return
        if not plugin.enabled or not plugin.cleanup_complete:
            raise RuntimeError("Plugin is disabled or has unfinished cleanup")
        for name in plugin.dependencies:
            if self._closing:
                raise RuntimeError("The host is shutting down")
            dependency = get_plugin(name)
            if dependency is None:
                raise RuntimeError(f"Missing plugin dependency: {name}")
            await self._start_plugin(dependency)
        if self._closing:
            raise RuntimeError("The host is shutting down")
        if get_plugin(plugin.id_) is not plugin:
            raise RuntimeError(
                "Plugin declarations were revoked during dependency startup"
            )
        plugin.status = PluginStatus.STARTING
        plugin.generation += 1
        plugin.cleanup_complete = False
        plugin.failure = None
        plugin.completed_shutdown_hooks.clear()
        try:
            if plugin.metadata and plugin.metadata.config:
                plugin.metadata.config.model_validate(
                    plugin.config_values or {}
                )
            for entries in (self._startup_funcs, self._ready_funcs):
                await self._owned_phase(entries, plugin)
            plugin.status = PluginStatus.READY
            await self._owned_phase(self._started_funcs, plugin)
        except BaseException as error:
            plugin.status = PluginStatus.STOPPING
            plugin.failure = error
            try:
                await self._close_plugin(plugin)
            finally:
                plugin.status = PluginStatus.FAILED
            raise

    async def _stop_plugin(self, plugin: "Plugin") -> None:
        from opsmesh.plugins.model import PluginStatus

        self.check_stoppable(plugin)
        if (
            plugin.status in (PluginStatus.STOPPED, PluginStatus.LOADED)
            and plugin.cleanup_complete
        ):
            plugin.status = PluginStatus.STOPPED
            return
        plugin.status = PluginStatus.STOPPING
        try:
            await self._close_plugin(plugin)
        except BaseException as error:
            plugin.status = PluginStatus.FAILED
            plugin.failure = error
            raise
        plugin.status = PluginStatus.STOPPED

    async def _close_plugin(self, plugin: "Plugin") -> None:
        from opsmesh.plugins.invocation import drain_plugin

        plugin.cleanup_complete = False
        with anyio.fail_after(self.shutdown_timeout, shield=True):
            await self._owned_phase(self._quiescing_funcs, plugin)
            await self._owned_phase(self._stopping_funcs, plugin)
            await drain_plugin(plugin, self.shutdown_timeout / 2)
            await self._shutdown_owned(plugin)
            while plugin.activation_cleanups:
                plugin.activation_cleanups[-1]()
                plugin.activation_cleanups.pop()
        plugin.cleanup_complete = True

    async def _shutdown_owned(self, plugin: "Plugin") -> None:
        for entry in reversed(tuple(self._shutdown_funcs)):
            if (
                entry.plugin is plugin
                and id(entry.func) not in plugin.completed_shutdown_hooks
            ):
                await self._owned_phase([entry], plugin)
                plugin.completed_shutdown_hooks.add(id(entry.func))

    async def shutdown(
        self,
        *,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        """Drain work and release plugin resources before closing tasks."""
        from opsmesh.plugins.invocation import drain_plugin
        from opsmesh.plugins.model import PluginStatus
        from opsmesh.plugins.registry import get_plugins_in_dependency_order

        task_group = self.task_group
        self._closing = True
        errors: list[BaseException] = []
        failed: set[str] = set()
        plugins: list[Plugin] = []

        async def phase(entries: list[_Entry]) -> None:
            for entry in reversed(tuple(entries)):
                plugin = entry.plugin
                if plugin is not None and plugin not in plugins:
                    continue
                try:
                    with anyio.fail_after(self.shutdown_timeout, shield=True):
                        await self._owned_phase([entry], plugin)
                except BaseException as error:
                    errors.append(error)
                    if plugin is not None:
                        failed.add(plugin.id_)
                        plugin.failure = error

        try:
            with anyio.CancelScope(shield=True):
                acquired = False
                with anyio.move_on_after(self.shutdown_timeout / 2):
                    await self._management_lock.acquire()
                    acquired = True
                if not acquired:
                    for scope in tuple(self._management_scopes):
                        scope.cancel()
                    with anyio.fail_after(self.shutdown_timeout / 2):
                        await self._management_lock.acquire()
                try:
                    plugins = [
                        plugin
                        for plugin in get_plugins_in_dependency_order()
                        if not plugin.cleanup_complete
                    ]
                finally:
                    self._management_lock.release()
                for plugin in plugins:
                    plugin.status = PluginStatus.STOPPING
                await phase(self._quiescing_funcs)
                await phase(self._stopping_funcs)
                for plugin in reversed(plugins):
                    try:
                        await drain_plugin(plugin, self.shutdown_timeout)
                        if any(
                            plugin.id_ in consumer.dependencies
                            and not consumer.cleanup_complete
                            for consumer in plugins
                        ):
                            raise RuntimeError(
                                "Dependent plugin cleanup failed"
                            )
                        with anyio.fail_after(
                            self.shutdown_timeout, shield=True
                        ):
                            await self._shutdown_owned(plugin)
                        while plugin.activation_cleanups:
                            plugin.activation_cleanups[-1]()
                            plugin.activation_cleanups.pop()
                        plugin.cleanup_complete = plugin.id_ not in failed
                        plugin.status = (
                            PluginStatus.STOPPED
                            if plugin.cleanup_complete
                            else PluginStatus.FAILED
                        )
                    except BaseException as error:
                        plugin.failure = error
                        plugin.status = PluginStatus.FAILED
                        errors.append(error)
                await phase(
                    [
                        entry
                        for entry in self._shutdown_funcs
                        if entry.plugin is None
                    ]
                )
                for plugin in get_plugins_in_dependency_order():
                    if (
                        plugin.cleanup_complete
                        and plugin.status is PluginStatus.FAILED
                    ):
                        plugin.status = PluginStatus.STOPPED
        finally:
            task_group.cancel_scope.cancel()
            try:
                await task_group.__aexit__(exc_type, exc_val, exc_tb)
            except BaseException as error:
                errors.append(error)
            finally:
                self._task_group = None
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
