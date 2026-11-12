"""Framework configuration, logical connections and lifecycle ownership."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast, overload

from anyio import CancelScope, create_task_group
from anyio.abc import TaskGroup

from opsmesh.config import Config, Env
from opsmesh.dependencies import Dependent
from opsmesh.dependencies.params import (
    ChannelParam,
    DefaultParam,
    DependParam,
)
from opsmesh.exceptions import SkippedException
from opsmesh.lifecycle import LIFESPAN_FUNC, Lifespan
from opsmesh.typing import (
    T_ConnectionHook,
    T_DependencyCache,
    T_DisconnectionHook,
)
from opsmesh.utils import flatten_exception_group, run_coro_with_catch

if TYPE_CHECKING:
    from opsmesh.adapters import Adapter, Channel

logger = logging.getLogger(__name__)
A = TypeVar("A", bound="Adapter")

CONNECTION_HOOK_PARAMS = [DependParam, ChannelParam, DefaultParam]


class Driver:
    """Own protocol-independent framework driver state for one host process."""

    _connection_hook: ClassVar[set[Dependent[Any]]] = set()
    _disconnection_hook: ClassVar[set[Dependent[Any]]] = set()

    def __init__(self, env: Env, config: Config) -> None:
        """Capture configuration and prepare empty logical registries."""
        self.env: str = env.environment
        """The runtime environment name."""
        self.config: Config = config.model_copy(deep=True)
        """The runtime-owned deployment configuration snapshot."""
        self._adapters: dict[str, Adapter] = {}
        self._channels: dict[str, Channel] = {}
        self._lifespan = Lifespan()

    def __repr__(self) -> str:
        return (
            f"Driver(environment={self.env!r}, "
            f"adapters={len(self._adapters)}, channels={len(self._channels)})"
        )

    @property
    def adapters(self) -> dict[str, Adapter]:
        """A copy of the adapters owned by this runtime instance."""
        return self._adapters.copy()

    @property
    def channels(self) -> dict[str, Channel]:
        """An independent snapshot of the currently connected channels."""
        return self._channels.copy()

    @property
    def task_group(self) -> TaskGroup:
        """The task group active during the runtime lifespan."""
        return self._lifespan.task_group

    def register_adapter(self, adapter: type[A], **kwargs: Any) -> A:
        """Construct and register a protocol adapter exactly once."""
        name = adapter.get_name()
        if name in self._adapters:
            raise ValueError(f'Adapter "{name}" is already registered')
        instance = adapter(self, **kwargs)
        self._adapters[name] = instance
        return instance

    @overload
    def get_adapter(self, name: str) -> Adapter: ...

    @overload
    def get_adapter(self, name: type[A]) -> A: ...

    def get_adapter(self, name: str | type[A]) -> Adapter | A:
        """Find an adapter by protocol name or concrete adapter class."""
        key = name if isinstance(name, str) else name.get_name()
        adapter = self._adapters[key]
        if not isinstance(name, str) and not isinstance(adapter, name):
            raise TypeError(f'Adapter "{key}" has another implementation')
        return cast(A, adapter)

    def get_channel(self, connection_id: str) -> Channel:
        """Find one connected endpoint by its explicit connection ID."""
        return self._channels[connection_id]

    @property
    def started(self) -> bool:
        """Whether the runtime owns an active lifecycle task group."""
        return self._lifespan.started

    async def startup(self) -> None:
        """Start framework callbacks and the process task group."""
        await self._lifespan.startup()

    async def shutdown(self) -> None:
        """Drain entry callbacks before releasing plugin resources."""
        await self._lifespan.shutdown()

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        """Own startup and shutdown within the same host task."""
        await self.startup()
        try:
            yield
        finally:
            await self.shutdown()

    def on_ready(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback after startup callbacks have completed."""
        return self._lifespan.on_ready(func)

    def on_startup(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a startup callback."""
        return self._lifespan.on_startup(func)

    def on_shutdown(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a shutdown callback."""
        return self._lifespan.on_shutdown(func)

    def on_stopping(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register work to drain before plugin resources are closed."""
        return self._lifespan.on_stopping(func)

    @classmethod
    def on_connect(cls, func: T_ConnectionHook) -> T_ConnectionHook:
        """Register a callback for successful channel connections.

        Hook arguments:

        channel: The connected channel instance.
        """
        from opsmesh.plugins.registry import register_hook

        return register_hook(cls._connection_hook, func, CONNECTION_HOOK_PARAMS)

    @classmethod
    def on_disconnect(cls, func: T_DisconnectionHook) -> T_DisconnectionHook:
        """Register a callback for channel disconnections.

        Hook arguments:

        channel: The connected channel instance.
        """
        from opsmesh.plugins.registry import register_hook

        return register_hook(
            cls._disconnection_hook, func, CONNECTION_HOOK_PARAMS
        )

    def connect(self, channel: Channel) -> None:
        """Register a channel after its connection succeeds."""
        if channel.self_id in self._channels:
            raise RuntimeError(
                f"Duplicate channel connection with id {channel.self_id}"
            )
        self._channels[channel.self_id] = channel

        if not self._connection_hook:
            return

        self.task_group.start_soon(
            self._run_hooks, self._connection_hook, channel
        )

    def disconnect(self, channel: Channel) -> None:
        """Unregister a channel after its connection closes."""
        if self._channels.get(channel.self_id) is not channel:
            raise ValueError(f"Channel {channel.self_id} is not connected")
        del self._channels[channel.self_id]
        if self._disconnection_hook:
            self.task_group.start_soon(
                self._run_hooks, self._disconnection_hook, channel
            )

    async def _run_hooks(
        self, hooks: set[Dependent[Any]], channel: Channel
    ) -> None:
        dependency_cache: T_DependencyCache = {}
        with CancelScope(shield=True):
            try:
                async with (
                    AsyncExitStack() as stack,
                    create_task_group() as task_group,
                ):
                    for hook in tuple(hooks):
                        task_group.start_soon(
                            run_coro_with_catch,
                            hook(
                                channel=channel,
                                stack=stack,
                                dependency_cache=dependency_cache,
                            ),
                            (SkippedException,),
                        )
            except* Exception as errors:
                for error in flatten_exception_group(errors):
                    logger.error(
                        "Channel lifecycle hook failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )

    def on_started(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register ingress startup after all plugins become ready."""
        return self._lifespan.on_started(func)

    def on_quiescing(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Stop and drain external inputs before stopping consumers."""
        return self._lifespan.on_quiescing(func)


_driver: Driver | None = None


def get_driver() -> Driver:
    """Return the initialized framework driver.

    Raises:
        ValueError: Framework initialization has not completed.
    """
    if _driver is None:
        raise ValueError("Framework has not been initialized.")
    return _driver


def is_initialized() -> bool:
    """Return whether framework initialization has completed."""
    return _driver is not None


def set_driver(driver: Driver) -> None:
    """Bind the framework driver after its construction succeeds.

    Args:
        driver: The protocol-independent driver owned by the host.

    Raises:
        RuntimeError: The host already has an initialized runtime.
    """
    global _driver
    if _driver is not None:
        raise RuntimeError("Framework has already been initialized.")
    _driver = driver
