"""Coordinate plugin activation, call draining and resource cleanup."""

import asyncio
import inspect
import logging
from collections.abc import Iterable
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, cast

import anyio
from anyio.abc import TaskGroup

from opsmesh.plugins.lifecycle import (
    ASYNC_LIFESPAN_FUNC,
    LIFESPAN_FUNC,
    SYNC_LIFESPAN_FUNC,
)
from opsmesh.utils import is_coroutine_callable, run_sync

if TYPE_CHECKING:
    from opsmesh.drivers import Driver
    from opsmesh.plugins.model import Plugin

logger = logging.getLogger(__name__)
_ACTIVATION_PHASES = ("startup", "ready", "started")

_managing: ContextVar[asyncio.Task | None] = ContextVar(
    "plugin_management", default=None
)
_changing: ContextVar[asyncio.Task | None] = ContextVar(
    "plugin_change", default=None
)


@dataclass(frozen=True)
class _Entry:
    func: LIFESPAN_FUNC
    plugin: "Plugin | None"
    identity: int | None = None


class Lifespan:
    """Start plugins, drain admitted work, then release plugin resources.

    Callbacks registered while a plugin is being imported belong to that
    plugin. Their startup order follows the recorded plugin dependency graph,
    and a failed non-required plugin only blocks its dependents. Callbacks
    registered outside plugin loading run in registration order.
    All stopping callbacks finish before any shutdown callback runs;
    each shutdown phase runs in reverse registration order.
    """

    def __init__(self, driver: "Driver | None" = None) -> None:
        self._driver = driver
        self._task_group: TaskGroup | None = None
        self._management_lock = anyio.Lock()
        self._closing = False
        self._management_scopes: set[anyio.CancelScope] = set()
        self.shutdown_timeout = 30.0

        self._callbacks: dict[str, list[_Entry]] = {}

    def _declared(self, phase: str, plugin: "Plugin") -> list[_Entry]:
        phases = ("prepare", phase) if phase == "startup" else (phase,)
        return [
            _Entry(hook.bind(self._driver), plugin, id(hook))
            for name in phases
            for hook in plugin.lifecycle_hooks.get(name, ())
        ]

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

    def _register(self, phase: str, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        from opsmesh.plugins.registry import (
            _current_plugin,
            track_registration,
        )

        callbacks = self._callbacks.setdefault(phase, [])
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
        return self._register("startup", func)

    def on_shutdown(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback to run during shutdown in reverse order."""
        return self._register("shutdown", func)

    def on_stopping(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register work to drain before resource shutdown callbacks run."""
        return self._register("stopping", func)

    def on_ready(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback to run after all startup callbacks finish."""
        return self._register("ready", func)

    def on_started(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register input activation after the global readiness barrier."""
        return self._register("started", func)

    def on_quiescing(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register input shutdown before consumer stopping callbacks."""
        return self._register("quiescing", func)

    @staticmethod
    async def _run_lifespan_func(
        funcs: Iterable[LIFESPAN_FUNC],
    ) -> None:
        for func in funcs:
            if inspect.iscoroutinefunction(func) or is_coroutine_callable(func):
                await cast(ASYNC_LIFESPAN_FUNC, func)()
            else:
                await run_sync(cast(SYNC_LIFESPAN_FUNC, func))()

    async def _run_phase(self, phase: str) -> None:
        from opsmesh.plugins.model import PluginStatus
        from opsmesh.plugins.registry import (
            get_plugins_in_dependency_order,
        )

        blocked: set[str] = set()
        plugins = get_plugins_in_dependency_order()

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
            try:
                await self._activate_phase(plugin, phase)
            except Exception as error:
                if plugin.required:
                    raise
                blocked.add(plugin.id_)
                logger.error(
                    'Plugin "%s" failed during startup; dependents are blocked',
                    plugin.id_,
                    exc_info=error,
                )

        await self._run_lifespan_func(
            entry.func for entry in self._entries(phase, None)
        )

    async def _activate_phase(self, plugin: "Plugin", phase: str) -> None:
        """Advance a single plugin through the shared activation phases."""
        from opsmesh.plugins.model import PluginStatus

        try:
            if phase == "startup":
                await self._prepare_activation(plugin)
            await self._owned_phase(phase, plugin)
            if phase == "ready":
                plugin.status = PluginStatus.READY
            elif phase == "started":
                plugin.manually_stopped = False
        except BaseException as error:
            try:
                await self._close_plugin(plugin)
            except BaseException as cleanup_error:
                error = BaseExceptionGroup(
                    "Plugin startup and cleanup failed", [error, cleanup_error]
                )
            plugin.failure = error
            plugin.status = PluginStatus.FAILED
            raise

    async def _prepare_activation(self, plugin: "Plugin") -> None:
        from opsmesh.plugins.config import validate_dependency_config
        from opsmesh.plugins.model import PluginStatus

        plugin.status = PluginStatus.STARTING
        plugin.generation += 1
        plugin.cleanup_complete = False
        plugin.failure = None
        plugin.completed_shutdown_hooks.clear()
        with self._callback_scope(plugin):
            if plugin.manager.prepare_config is not None:
                await plugin.manager.prepare_config(plugin)
            validate_dependency_config(plugin)
            if plugin.metadata and plugin.metadata.config:
                plugin.metadata.config.model_validate(
                    plugin.config_values or {}
                )

    async def startup(self) -> None:
        """Enter the task group and invoke startup and readiness callbacks."""
        self._closing = False
        self.task_group = anyio.create_task_group()
        await self.task_group.__aenter__()
        try:
            for phase in _ACTIVATION_PHASES:
                await self._run_phase(phase)
        except BaseException:
            await self.shutdown()
            raise

    def check_stoppable(self, plugin: "Plugin") -> None:
        """Reject self-stop and active consumers before changing resources."""
        from opsmesh.plugins.errors import PluginOperationBlocked
        from opsmesh.plugins.model import PluginStatus
        from opsmesh.plugins.registry import get_loaded_plugins

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
            raise PluginOperationBlocked(
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
            try:
                async with self.change():
                    await operation(plugin)
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

        self.task_group.start_soon(execute)
        with anyio.CancelScope(shield=True):
            await done.wait()
        if errors:
            raise errors[0]

    @asynccontextmanager
    async def change(self):
        """Serialize changes that must not race with activation or shutdown."""
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

    @staticmethod
    @contextmanager
    def _callback_scope(plugin: "Plugin | None"):
        from opsmesh.plugins.registry import plugin_scope

        management = _managing.set(asyncio.current_task())
        try:
            with plugin_scope(plugin):
                yield
        finally:
            _managing.reset(management)

    def _entries(self, phase: str, plugin: "Plugin | None") -> list[_Entry]:
        declared = self._declared(phase, plugin) if plugin is not None else []
        return declared + [
            entry
            for entry in self._callbacks.get(phase, ())
            if entry.plugin is plugin
        ]

    async def _owned_phase(self, phase: str, plugin: "Plugin") -> None:
        with self._callback_scope(plugin):
            await self._run_lifespan_func(
                entry.func for entry in self._entries(phase, plugin)
            )

    async def _run_entry(self, entry: _Entry) -> None:
        with self._callback_scope(entry.plugin):
            await self._run_lifespan_func((entry.func,))

    async def _start_plugin(
        self, plugin: "Plugin", *, dependency: bool = False
    ) -> None:
        from opsmesh.plugins.errors import PluginOperationBlocked
        from opsmesh.plugins.registry import (
            get_plugin,
            get_plugins_in_dependency_order,
        )

        if get_plugin(plugin.id_) is not plugin:
            raise RuntimeError(
                "Plugin declarations were revoked before startup"
            )
        if plugin.ready:
            return
        if dependency and plugin.manually_stopped:
            raise PluginOperationBlocked(
                f"Start manually stopped dependency first: {plugin.id_}"
            )
        if not plugin.enabled or not plugin.cleanup_complete:
            raise PluginOperationBlocked(
                f"Plugin is disabled or has unfinished cleanup: {plugin.id_}"
            )
        if not dependency:
            get_plugins_in_dependency_order()
        for name in plugin.dependencies:
            if self._closing:
                raise RuntimeError("The host is shutting down")
            provider = get_plugin(name)
            if provider is None:
                raise RuntimeError(f"Missing plugin dependency: {name}")
            await self._start_plugin(provider, dependency=True)
        if self._closing:
            raise RuntimeError("The host is shutting down")
        if get_plugin(plugin.id_) is not plugin:
            raise RuntimeError(
                "Plugin declarations were revoked during dependency startup"
            )
        for phase in _ACTIVATION_PHASES:
            await self._activate_phase(plugin, phase)

    async def _stop_plugin(self, plugin: "Plugin") -> None:
        from opsmesh.plugins.model import PluginStatus

        self.check_stoppable(plugin)
        plugin.manually_stopped = True
        if (
            plugin.status in (PluginStatus.STOPPED, PluginStatus.LOADED)
            and plugin.cleanup_complete
        ):
            plugin.status = PluginStatus.STOPPED
            return
        await self._close_plugin(plugin)

    async def _close_plugin(self, plugin: "Plugin") -> None:
        from opsmesh.plugins.model import PluginStatus

        plugin.status = PluginStatus.STOPPING
        plugin.cleanup_complete = False
        try:
            with anyio.fail_after(self.shutdown_timeout, shield=True):
                await self._owned_phase("quiescing", plugin)
                await self._owned_phase("stopping", plugin)
                await self._release_plugin(
                    plugin, drain_timeout=self.shutdown_timeout / 2
                )
        except BaseException as error:
            plugin.status = PluginStatus.FAILED
            plugin.failure = error
            raise

    async def _release_plugin(
        self,
        plugin: "Plugin",
        *,
        drain_timeout: float,
        consumers: Iterable["Plugin"] = (),
        input_failed: bool = False,
    ) -> None:
        """Release resources after intake has stopped and calls can drain."""
        from opsmesh.plugins.invocation import drain_plugin
        from opsmesh.plugins.model import PluginStatus

        try:
            await drain_plugin(plugin, drain_timeout)
            if any(
                plugin.id_ in consumer.dependencies
                and not consumer.cleanup_complete
                for consumer in consumers
            ):
                raise RuntimeError("Dependent plugin cleanup failed")
            with anyio.fail_after(self.shutdown_timeout):
                await self._shutdown_owned(plugin)
                while plugin.activation_cleanups:
                    plugin.activation_cleanups[-1]()
                    plugin.activation_cleanups.pop()
        except BaseException as error:
            plugin.failure = error
            plugin.status = PluginStatus.FAILED
            raise
        plugin.cleanup_complete = not input_failed
        plugin.status = (
            PluginStatus.FAILED if input_failed else PluginStatus.STOPPED
        )

    async def _shutdown_owned(self, plugin: "Plugin") -> None:
        for entry in reversed(self._entries("shutdown", plugin)):
            key = entry.identity or id(entry)
            if (
                entry.plugin is plugin
                and key not in plugin.completed_shutdown_hooks
            ):
                await self._run_entry(entry)
                plugin.completed_shutdown_hooks.add(key)

    async def shutdown(
        self,
        *,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        """Drain work and release plugin resources before closing tasks."""
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
                        await self._run_entry(entry)
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
                for name in ("quiescing", "stopping"):
                    recorded = [
                        entry
                        for plugin in plugins
                        for entry in self._declared(name, plugin)
                    ]
                    await phase(recorded + self._callbacks.get(name, []))
                for plugin in reversed(plugins):
                    try:
                        await self._release_plugin(
                            plugin,
                            drain_timeout=self.shutdown_timeout,
                            consumers=plugins,
                            input_failed=plugin.id_ in failed,
                        )
                    except BaseException as error:
                        errors.append(error)
                await phase(self._entries("shutdown", None))
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
