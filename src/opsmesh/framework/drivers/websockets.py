"""Optional websocket client driver with cancellation-safe connection cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from websockets.asyncio.client import ClientConnection, connect

from . import DriverState, OpsMeshDriver, WebSocket, WebSocketClient


class WebSocketConnection(WebSocket):
    def __init__(self, connection: ClientConnection) -> None:
        self.connection = connection

    async def send(self, data: str | bytes) -> None:
        await self.connection.send(data)

    async def receive(self) -> str | bytes:
        return await self.connection.recv()

    async def close(self, code: int = 1000) -> None:
        await self.connection.close(code=code)


class WebSocketDriver(OpsMeshDriver, WebSocketClient):
    capabilities = frozenset({"websocket_client"})

    def __init__(self) -> None:
        super().__init__()
        self._connections: set[WebSocketConnection] = set()
        self.on_shutdown(self._close_connections)

    async def _close_connections(self) -> None:
        connections = tuple(self._connections)
        self._connections.clear()
        results = await asyncio.gather(
            *(connection.close() for connection in connections), return_exceptions=True
        )
        errors = [error for error in results if isinstance(error, Exception)]
        if errors:
            raise ExceptionGroup("WebSocket connections could not be closed", errors)

    @asynccontextmanager
    async def websocket(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> AsyncIterator[WebSocket]:
        if self.state not in (DriverState.STARTING, DriverState.RUNNING):
            raise RuntimeError("WebSocket driver is not running")
        async with connect(url, additional_headers=headers) as connection:
            # A connection handshake can finish after shutdown has begun.
            if self.state not in (DriverState.STARTING, DriverState.RUNNING):
                raise RuntimeError("WebSocket driver stopped during connection")
            managed = WebSocketConnection(connection)
            self._connections.add(managed)
            try:
                yield managed
            finally:
                self._connections.discard(managed)
