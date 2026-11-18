"""Framework configuration and lifecycle ownership."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from anyio.abc import TaskGroup

from opsmesh.config import Config, Env
from opsmesh.plugins.activation import Lifespan
from opsmesh.plugins.lifecycle import LIFESPAN_FUNC

if TYPE_CHECKING:
    from opsmesh.plugins.model import Plugin


class Driver:
    """Own protocol-independent framework driver state for one host process."""

    def __init__(self, env: Env, config: Config) -> None:
        """Capture configuration and prepare the plugin lifecycle."""
        self.env: str = env.environment
        """The runtime environment name."""
        self.config: Config = config.model_copy(deep=True)
        """The runtime-owned deployment configuration snapshot."""
        self._lifespan = Lifespan(self)

    def __repr__(self) -> str:
        return f"Driver(environment={self.env!r})"

    @property
    def task_group(self) -> TaskGroup:
        """The task group active during the runtime lifespan."""
        return self._lifespan.task_group

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
    async def plugin_change(self) -> AsyncIterator[None]:
        """Serialize a live plugin-management change with host shutdown."""
        async with self._lifespan.change():
            yield

    def check_plugin_stoppable(self, plugin: Plugin) -> None:
        """Reject stopping a plugin with active consumers or its own call."""
        self._lifespan.check_stoppable(plugin)

    async def start_plugin(self, plugin: Plugin) -> None:
        """Start one loaded plugin through the framework lifecycle."""
        await self._lifespan.start_plugin(plugin)

    async def stop_plugin(self, plugin: Plugin) -> None:
        """Stop one loaded plugin through the framework lifecycle."""
        await self._lifespan.stop_plugin(plugin)

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

    def on_started(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register ingress startup after all plugins become ready."""
        return self._lifespan.on_started(func)

    def on_quiescing(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Stop and drain external inputs before stopping consumers."""
        return self._lifespan.on_quiescing(func)
