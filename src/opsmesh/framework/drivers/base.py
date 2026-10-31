"""Async process lifecycle, supervised tasks and optional network capabilities."""

from __future__ import annotations

import asyncio
import contextvars
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar

from opsmesh.framework.dependencies import CallableDependency, DependencyScope
from opsmesh.framework.drivers.models import (
    HTTPRequest,
    HTTPResponse,
    HTTPServerSetup,
    WebSocketServerSetup,
)

if TYPE_CHECKING:
    from opsmesh.framework.adapters import OpsMeshAdapter
    from opsmesh.framework.plugin import OpsMeshPlugin

T = TypeVar("T")


class DriverState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class OpsMeshDriver:
    capabilities: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.state = DriverState.NEW
        self.adapters: dict[str, OpsMeshAdapter] = {}
        self._startup: list[CallableDependency] = []
        self._shutdown: list[CallableDependency] = []
        self._hook_enabled: dict[int, Callable[[], bool]] = {}
        self._hook_plugins: dict[int, OpsMeshPlugin] = {}
        self._shutdown_allowed: set[int] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.task_errors: list[BaseException] = []

    def _hook(self, fn: Callable[..., Any], target: list[CallableDependency]) -> Callable[..., Any]:
        from opsmesh.framework.plugin import get_current_plugin

        if self.state != DriverState.NEW:
            raise RuntimeError("Lifecycle hooks must be registered before startup")
        dependent = CallableDependency.parse(fn)
        target.append(dependent)
        if plugin := get_current_plugin():
            self._hook_enabled[id(dependent)] = lambda: plugin.enabled
            self._hook_plugins[id(dependent)] = plugin

            def remove_hook() -> None:
                if dependent in target:
                    target.remove(dependent)
                self._hook_enabled.pop(id(dependent), None)
                self._hook_plugins.pop(id(dependent), None)

            plugin.own(remove_hook)
        return fn

    async def _invoke_hook(self, scope: DependencyScope, hook: CallableDependency) -> None:
        from opsmesh.framework.plugin.manager import _current_plugin

        token = _current_plugin.set(self._hook_plugins.get(id(hook)))
        try:
            await scope.invoke(hook)
        finally:
            _current_plugin.reset(token)

    def on_startup(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        return self._hook(fn, self._startup)

    def on_shutdown(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        return self._hook(fn, self._shutdown)

    def register_adapter(
        self, adapter_type: type[OpsMeshAdapter] | OpsMeshAdapter
    ) -> OpsMeshAdapter:
        from opsmesh.framework.adapters import OpsMeshAdapter
        from opsmesh.framework.plugin import get_current_plugin

        if self.state != DriverState.NEW:
            raise RuntimeError("Adapters must be registered before startup")
        missing = adapter_type.required_capabilities - self.capabilities
        if missing:
            raise ValueError(f"OpsMeshDriver lacks adapter capabilities: {sorted(missing)}")
        adapter = adapter_type if isinstance(adapter_type, OpsMeshAdapter) else adapter_type(self)
        if adapter.driver is not self:
            raise ValueError("OpsMeshAdapter belongs to another driver")
        if adapter.name in self.adapters:
            raise ValueError(f"OpsMeshAdapter already registered: {adapter.name}")
        owner = get_current_plugin()
        adapter._bind_registration(owner)
        self.adapters[adapter.name] = adapter
        if owner is not None:

            def remove_adapter() -> None:
                if self.adapters.get(adapter.name) is adapter:
                    self.adapters.pop(adapter.name)
                adapter._remove_registration()

            owner.own(remove_adapter)
        return adapter

    async def start(self) -> None:
        if self.state != DriverState.NEW:
            raise RuntimeError(f"Cannot start driver in state {self.state}")
        self.state = DriverState.STARTING
        self._shutdown_allowed = {
            id(hook) for hook in self._shutdown if self._hook_enabled.get(id(hook), lambda: True)()
        }
        try:
            async with DependencyScope({"driver": self}) as scope:
                for hook in tuple(self._startup):
                    if self._hook_enabled.get(id(hook), lambda: True)():
                        await self._invoke_hook(scope, hook)
        except BaseException:
            await self.stop()
            raise
        self.state = DriverState.RUNNING

    def create_task(
        self,
        coroutine: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
        inherit_context: bool = False,
    ) -> asyncio.Task[T]:
        if self.state not in (DriverState.STARTING, DriverState.RUNNING):
            coroutine.close()
            raise RuntimeError("OpsMeshDriver is not accepting background work")
        context = contextvars.copy_context() if inherit_context else contextvars.Context()
        task = asyncio.create_task(coroutine, name=name, context=context)
        self._tasks.add(task)

        def done(completed: asyncio.Task[Any]) -> None:
            self._tasks.discard(completed)
            if not completed.cancelled() and (error := completed.exception()) is not None:
                self.task_errors.append(error)
                del self.task_errors[:-100]

        task.add_done_callback(done)
        return task

    async def stop(self) -> None:
        if self.state in (DriverState.NEW, DriverState.STOPPED):
            self.state = DriverState.STOPPED
            return
        if self.state == DriverState.STOPPING:
            return
        self.state = DriverState.STOPPING
        errors: list[Exception] = []
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            async with DependencyScope({"driver": self}) as scope:
                for hook in reversed(self._shutdown):
                    if id(hook) not in self._shutdown_allowed:
                        continue
                    try:
                        await self._invoke_hook(scope, hook)
                    except Exception as exc:
                        errors.append(exc)
        finally:
            self.state = DriverState.STOPPED
        if errors:
            raise ExceptionGroup("OpsMeshDriver shutdown failed", errors)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[OpsMeshDriver]:
        await self.start()
        try:
            yield self
        finally:
            await self.stop()


class HTTPClient(ABC):
    @abstractmethod
    async def request(self, request: HTTPRequest) -> HTTPResponse:
        """Execute an HTTP request."""


class WebSocket(ABC):
    @abstractmethod
    async def send(self, data: str | bytes) -> None:
        """Send a frame."""

    @abstractmethod
    async def receive(self) -> str | bytes:
        """Receive a frame, raising on disconnection."""

    @abstractmethod
    async def close(self, code: int = 1000) -> None:
        """Close the connection."""


class WebSocketClient(ABC):
    @abstractmethod
    def websocket(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> AbstractAsyncContextManager[WebSocket]:
        """Connect to a websocket with explicit resource ownership."""


class WebSocketServerConnection(WebSocket):
    @abstractmethod
    async def accept(self, subprotocol: str | None = None) -> None:
        """Accept an incoming connection after the handler authenticates its request."""


class HTTPServer(ABC):
    @abstractmethod
    def setup_http_server(self, setup: HTTPServerSetup) -> None:
        """Register an inbound HTTP handler before the server route table is frozen."""

    @property
    @abstractmethod
    def http_server_setups(self) -> tuple[HTTPServerSetup, ...]:
        """Snapshot of registered, ownership-checked HTTP handlers."""

    @abstractmethod
    def freeze_server_routes(self) -> None:
        """Prevent registrations after ASGI routes have been mounted."""


class WebSocketServer(ABC):
    @abstractmethod
    def setup_websocket_server(self, setup: WebSocketServerSetup) -> None:
        """Register an inbound WebSocket handler before mounting."""

    @property
    @abstractmethod
    def websocket_server_setups(self) -> tuple[WebSocketServerSetup, ...]:
        """Snapshot of registered, ownership-checked WebSocket handlers."""

    @abstractmethod
    def freeze_server_routes(self) -> None:
        """Prevent registrations after ASGI routes have been mounted."""
