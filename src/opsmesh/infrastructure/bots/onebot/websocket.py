"""OneBot universal WebSocket API correlation, bounded events and reconnect ownership.

Protocol: https://github.com/botuniverse/onebot-11/blob/master/communication/ws.md
Reverse: https://github.com/botuniverse/onebot-11/blob/master/communication/ws-reverse.md
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from opsmesh.framework.adapters import BotConnectionConfiguration
from opsmesh.framework.drivers import (
    DriverState,
    HTTPRequest,
    OpsMeshDriver,
    WebSocket,
    WebSocketClient,
    WebSocketServer,
    WebSocketServerConnection,
    WebSocketServerSetup,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.onebot.configuration import OneBotSettings
from opsmesh.infrastructure.bots.onebot.response import api_result

logger = logging.getLogger(__name__)
AccountLoader = Callable[[], Awaitable[list[BotConnectionConfiguration]]]
EventConsumer = Callable[[str, dict[str, Any]], Awaitable[None]]


class OneBotWebSocketSession:
    def __init__(self, socket: WebSocket, bot_id: str, consume: EventConsumer) -> None:
        self.socket, self.bot_id, self.consume = socket, bot_id, consume
        self.pending: dict[str, asyncio.Future[Any]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self.closed = False

    async def call(self, action: str, params: dict[str, Any]) -> Any:
        if self.closed:
            raise OpsMeshError(
                "BOT_UNAVAILABLE", "OneBot WebSocket is disconnected", retryable=True
            )
        if len(self.pending) >= 128:
            raise OpsMeshError(
                "BOT_RATE_LIMITED", "Too many concurrent OneBot API calls", retryable=True
            )
        echo = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[echo] = future
        try:
            await self.socket.send(json.dumps({"action": action, "params": params, "echo": echo}))
            return api_result(await asyncio.wait_for(future, 30))
        except OpsMeshError:
            raise
        except Exception as error:
            raise OpsMeshError(
                "DELIVERY_UNKNOWN", "OneBot WebSocket operation outcome is uncertain"
            ) from error
        finally:
            self.pending.pop(echo, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._receive())
                group.create_task(self._events())
        finally:
            self.closed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        OpsMeshError("DELIVERY_UNKNOWN", "OneBot disconnected during a call")
                    )
            await self.socket.close()

    async def _receive(self) -> None:
        while True:
            frame = await self.socket.receive()
            if len(frame.encode() if isinstance(frame, str) else frame) > 1024 * 1024:
                raise OpsMeshError("INPUT_TOO_LARGE", "OneBot frame exceeds 1 MiB")
            payload = json.loads(frame)
            if not isinstance(payload, dict):
                raise OpsMeshError("INVALID_REQUEST", "OneBot frame must be a JSON object")
            if "post_type" in payload:
                # Fail and reconnect on overflow; never deadlock API responses behind a handler.
                self.events.put_nowait(payload)
            elif isinstance(payload.get("echo"), str):
                future = self.pending.get(payload["echo"])
                if future is not None and not future.done():
                    future.set_result(payload)

    async def _events(self) -> None:
        while True:
            payload = await self.events.get()
            try:
                await self.consume(self.bot_id, payload)
            except OpsMeshError:
                logger.warning("OneBot event was rejected", extra={"bot_id": self.bot_id})
            except Exception:
                logger.exception("OneBot event dispatch failed", extra={"bot_id": self.bot_id})
            finally:
                self.events.task_done()


class OneBotConnectionManager:
    reverse_path = "/plugins/onebot/ws"

    def __init__(
        self,
        driver: OpsMeshDriver,
        accounts: AccountLoader,
        consume: EventConsumer,
        resolve_secret: Callable[[str], str],
        *,
        autostart: bool = True,
        on_connect: Callable[[BotConnectionConfiguration], Callable[[], None]] | None = None,
    ) -> None:
        self.driver, self.accounts, self.consume, self.resolve_secret = (
            driver,
            accounts,
            consume,
            resolve_secret,
        )
        self.autostart = autostart
        self.on_connect = on_connect
        self.sessions: dict[str, OneBotWebSocketSession] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        if isinstance(driver, WebSocketServer):
            driver.setup_websocket_server(WebSocketServerSetup(self.reverse_path, self.reverse))

    async def start(self) -> None:
        if self.autostart:
            for account in await self.accounts():
                self.connect(account)

    def connect(self, account: BotConnectionConfiguration) -> None:
        if (
            not self.autostart
            or not account.enabled
            or OneBotSettings.model_validate(account.settings).transport != "websocket"
        ):
            return
        if self.driver.state not in {DriverState.STARTING, DriverState.RUNNING}:
            return
        previous = self.tasks.get(account.id)
        if previous is None or previous.done():
            self.tasks[account.id] = self.driver.create_task(
                self._reconnect(account), name=f"onebot:{account.id}"
            )

    async def disconnect(self, bot_id: str) -> None:
        task = self.tasks.pop(bot_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        session = self.sessions.pop(bot_id, None)
        if session is not None:
            session.closed = True
            await session.socket.close(1001)

    async def close(self) -> None:
        for bot_id in set(self.tasks) | set(self.sessions):
            await self.disconnect(bot_id)

    async def call(self, bot_id: str, action: str, params: dict[str, Any]) -> Any:
        session = self.sessions.get(bot_id)
        if session is None:
            raise OpsMeshError(
                "BOT_UNAVAILABLE", "OneBot has no connected WebSocket", retryable=True
            )
        return await session.call(action, params)

    async def _serve(self, account: BotConnectionConfiguration, connection: WebSocket) -> None:
        previous = self.sessions.get(account.id)
        if previous is not None:
            await previous.socket.close(1012)
        session = OneBotWebSocketSession(connection, account.id, self.consume)
        self.sessions[account.id] = session
        disconnect = None
        try:
            disconnect = self.on_connect(account) if self.on_connect else None
            try:
                await session.run()
            except* (WebSocketDisconnect, ConnectionClosed):
                pass
        finally:
            if disconnect is not None:
                disconnect()
            if self.sessions.get(account.id) is session:
                self.sessions.pop(account.id, None)

    async def _reconnect(self, account: BotConnectionConfiguration) -> None:
        if not isinstance(self.driver, WebSocketClient):
            raise OpsMeshError("FEATURE_UNSUPPORTED", "Driver has no WebSocket client")
        settings = OneBotSettings.model_validate(account.settings)
        assert settings.websocket_url is not None
        delay = settings.reconnect_seconds
        while True:
            try:
                token = (
                    self.resolve_secret(account.credentials["access_token"])
                    if account.credentials.get("access_token")
                    else None
                )
                headers = {"Authorization": f"Bearer {token}"} if token else None
                async with self.driver.websocket(
                    settings.websocket_url, headers=headers
                ) as connection:
                    await self._serve(account, connection)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("OneBot disconnected; reconnecting", extra={"bot_id": account.id})
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def reverse(self, request: HTTPRequest, connection: WebSocketServerConnection) -> None:
        headers = {key.lower(): value for key, value in request.headers.items()}
        if headers.get("x-client-role", "").lower() != "universal":
            raise OpsMeshError(
                "UNSUPPORTED_FEATURE", "Configure the OneBot Universal reverse client"
            )
        accounts = [
            account
            for account in await self.accounts()
            if account.enabled
            and OneBotSettings.model_validate(account.settings).transport == "reverse_websocket"
            and account.self_id == headers.get("x-self-id")
        ]
        if len(accounts) != 1 or accounts[0].credentials.get("access_token") is None:
            raise OpsMeshError("UNAUTHORIZED", "Unknown reverse WebSocket account")
        account = accounts[0]
        assert account.credentials.get("access_token") is not None
        expected = "Bearer " + self.resolve_secret(account.credentials["access_token"])
        if not hmac.compare_digest(headers.get("authorization", ""), expected):
            raise OpsMeshError("UNAUTHORIZED", "Invalid reverse WebSocket credential")
        await connection.accept()
        await self._serve(account, connection)
