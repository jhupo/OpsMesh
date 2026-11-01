"""Owned inbound declarations and managed WebSocket connections, without an ASGI dependency."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

from opsmesh.framework.drivers.base import (
    DriverState,
    HTTPServer,
    OpsMeshDriver,
    WebSocketServer,
    WebSocketServerConnection,
)
from opsmesh.framework.drivers.models import (
    HTTPRequest,
    HTTPResponse,
    HTTPServerSetup,
    WebSocketServerSetup,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import OpsMeshPlugin, get_current_plugin


class GuardedWebSocketConnection(WebSocketServerConnection):
    def __init__(self, connection: WebSocketServerConnection, guard: Callable[[], None]) -> None:
        self.connection, self.guard = connection, guard

    async def accept(self, subprotocol: str | None = None) -> None:
        self.guard()
        await self.connection.accept(subprotocol)

    async def send(self, data: str | bytes) -> None:
        self.guard()
        await self.connection.send(data)

    async def receive(self) -> str | bytes:
        self.guard()
        data = await self.connection.receive()
        self.guard()
        return data

    async def close(self, code: int = 1000) -> None:
        await self.connection.close(code)


class ServerDriver(OpsMeshDriver, HTTPServer, WebSocketServer):
    """Cooperative mixin used by the network driver; registration does no network I/O."""

    def __init__(self) -> None:
        super().__init__()
        self._http_servers: list[HTTPServerSetup] = []
        self._websocket_servers: list[WebSocketServerSetup] = []
        self._server_routes_frozen = False
        self._server_connections: set[GuardedWebSocketConnection] = set()
        self.on_shutdown(self._close_server_connections)

    def _check_registration(self) -> None:
        if self._server_routes_frozen or self.state in {DriverState.STOPPING, DriverState.STOPPED}:
            raise OpsMeshError(
                "REGISTRY_FROZEN", "Server routes are frozen; restart to change them"
            )

    def _check_handler(self, owner: OpsMeshPlugin | None) -> None:
        if owner is not None and not owner.enabled:
            raise OpsMeshError("PLUGIN_DISABLED", "The server route's plugin is disabled")
        if self.state != DriverState.RUNNING:
            raise OpsMeshError("DRIVER_UNAVAILABLE", "The driver is not running", retryable=True)

    def setup_http_server(self, setup: HTTPServerSetup) -> None:
        self._check_registration()
        if any(
            item.path == setup.path and item.methods & setup.methods for item in self._http_servers
        ):
            raise OpsMeshError("ROUTE_CONFLICT", f"HTTP server route already exists: {setup.path}")
        owner = get_current_plugin()

        async def handle(request: HTTPRequest) -> HTTPResponse:
            self._check_handler(owner)
            return await setup.handler(request)

        registered = replace(setup, handler=handle)
        self._http_servers.append(registered)
        if owner is not None:
            owner.own(lambda: self._http_servers.remove(registered))

    def setup_websocket_server(self, setup: WebSocketServerSetup) -> None:
        self._check_registration()
        if any(item.path == setup.path for item in self._websocket_servers):
            raise OpsMeshError("ROUTE_CONFLICT", f"WebSocket route already exists: {setup.path}")
        owner = get_current_plugin()

        async def handle(request: HTTPRequest, connection: WebSocketServerConnection) -> None:
            self._check_handler(owner)
            guarded = GuardedWebSocketConnection(connection, lambda: self._check_handler(owner))
            self._server_connections.add(guarded)
            try:
                await setup.handler(request, guarded)
            finally:
                self._server_connections.discard(guarded)

        registered = replace(setup, handler=handle)
        self._websocket_servers.append(registered)
        if owner is not None:
            owner.own(lambda: self._websocket_servers.remove(registered))

    @property
    def http_server_setups(self) -> tuple[HTTPServerSetup, ...]:
        return tuple(self._http_servers)

    @property
    def websocket_server_setups(self) -> tuple[WebSocketServerSetup, ...]:
        return tuple(self._websocket_servers)

    def freeze_server_routes(self) -> None:
        self._server_routes_frozen = True

    async def _close_server_connections(self) -> None:
        connections = tuple(self._server_connections)
        self._server_connections.clear()
        results = await asyncio.gather(
            *(connection.close(1001) for connection in connections), return_exceptions=True
        )
        failures = [result for result in results if isinstance(result, Exception)]
        if failures:
            raise ExceptionGroup("Inbound WebSocket connections could not be closed", failures)
