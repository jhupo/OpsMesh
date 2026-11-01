"""Reconnect ownership for one QQ account; all events enter the platform consumer."""

import asyncio
import logging
import time

from opsmesh.framework.adapters import BotEventConsumer
from opsmesh.framework.drivers import WebSocketClient
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.qq.bot import QQBot
from opsmesh.infrastructure.bots.qq.configuration import trusted_gateway
from opsmesh.infrastructure.bots.qq.gateway import QQGatewaySession, QQGatewayState

logger = logging.getLogger(__name__)


class QQGatewayConnection:
    def __init__(self, driver: WebSocketClient, bot: QQBot, consume: BotEventConsumer) -> None:
        self.driver, self.bot, self.consume = driver, bot, consume
        self.state = QQGatewayState()
        self.session: QQGatewaySession | None = None
        self._gateway_url: str | None = None
        self._gateway_expires_at = 0.0

    @property
    def connected(self) -> bool:
        return self.session is not None and self.session.ready.is_set() and not self.session.closed

    async def _endpoint(self) -> str:
        if self._gateway_url is not None and time.monotonic() < self._gateway_expires_at:
            return self._gateway_url
        response = await self.bot.client.get("/gateway")
        endpoint = response.get("url")
        try:
            if not isinstance(endpoint, str):
                raise ValueError("Gateway URL is missing")
            endpoint = trusted_gateway(endpoint, self.bot.client.settings)
        except ValueError as error:
            raise OpsMeshError("BOT_PROTOCOL_ERROR", "QQ returned an untrusted Gateway") from error
        self._gateway_url = endpoint
        self._gateway_expires_at = time.monotonic() + 600
        return endpoint

    async def run(self, previous_task: asyncio.Task[None] | None = None) -> None:
        if previous_task is not None:
            await asyncio.gather(previous_task, return_exceptions=True)
        settings = self.bot.client.settings
        delay = settings.reconnect_seconds
        try:
            while True:
                established = False
                try:
                    endpoint = await self._endpoint()
                    token = await self.bot.client.access_token()
                    async with self.driver.websocket(endpoint) as socket:
                        session = QQGatewaySession(
                            socket, self.bot.configuration, settings, self.state, self.consume
                        )
                        self.session = session
                        try:
                            await session.run(token)
                        finally:
                            established = session.ready.is_set()
                            self.session = None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Provider exceptions can contain credentials, frames and socket URLs.
                    logger.warning(
                        "QQ Gateway disconnected", extra={"bot_id": self.bot.configuration.id}
                    )
                if established:
                    delay = settings.reconnect_seconds
                # Discovery itself is limited to 2 QPM; connection retries reuse a valid URL.
                await asyncio.sleep(delay if self._gateway_url is not None else max(30, delay))
                delay = min(60, delay * 2)
        finally:
            self.session = None
