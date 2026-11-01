"""DingTalk application bot implementation of the provider-neutral adapter lifecycle."""

import asyncio
import logging
from collections.abc import Callable
from typing import Literal
from urllib.parse import urlencode

import httpx

from opsmesh.framework.adapters import (
    BotAdapterConnectionStatus,
    BotAdapterDescriptor,
    BotConnectionConfiguration,
    OpsMeshBotAdapter,
)
from opsmesh.framework.drivers import DriverState, OpsMeshDriver, WebSocketClient
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.dingtalk.bot import DingTalkBot
from opsmesh.infrastructure.bots.dingtalk.client import DingTalkAPIClient
from opsmesh.infrastructure.bots.dingtalk.configuration import (
    DingTalkConfiguration,
    parse_configuration,
    trusted_endpoint,
)
from opsmesh.infrastructure.bots.dingtalk.stream import DingTalkStreamSession

logger = logging.getLogger(__name__)


class DingTalkAdapter(OpsMeshBotAdapter):
    name = "dingtalk"

    def __init__(
        self,
        driver: OpsMeshDriver,
        client: httpx.AsyncClient,
        resolve_secret: Callable[[str], str],
        *,
        autostart: bool = True,
    ) -> None:
        super().__init__(driver, client, resolve_secret, autostart=autostart)
        self._clients: dict[str, DingTalkBot] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.sessions: dict[str, DingTalkStreamSession] = {}

    def describe(self) -> BotAdapterDescriptor:
        return BotAdapterDescriptor(
            name=self.name,
            display_name="钉钉应用机器人",
            capabilities=frozenset(
                {"stream", "receive_messages", "send_text", "send_markdown", "private", "group"}
            ),
            settings_schema=DingTalkConfiguration.model_json_schema(),
            credential_names=frozenset({"client_secret"}),
        )

    def validate_configuration(self, configuration: BotConnectionConfiguration) -> None:
        parse_configuration(configuration)
        if self.autostart and not isinstance(self.driver, WebSocketClient):
            raise OpsMeshError(
                "CONFIG_INVALID", "DingTalk Stream requires a WebSocket client Driver"
            )

    def get_bot(self, configuration: BotConnectionConfiguration) -> DingTalkBot:
        self.validate_configuration(configuration)
        bot = self._clients.get(configuration.id)
        if bot is None or bot.configuration != configuration:
            bot = DingTalkBot(
                self,
                DingTalkAPIClient(
                    configuration,
                    parse_configuration(configuration),
                    self.client,
                    self.resolve_secret,
                ),
            )
            self._clients[configuration.id] = bot
        return bot

    def configure(self, configuration: BotConnectionConfiguration) -> None:
        self.validate_configuration(configuration)
        if not configuration.enabled:
            return
        self.get_bot(configuration)
        if not self.autostart or self.driver.state not in {
            DriverState.STARTING,
            DriverState.RUNNING,
        }:
            return
        task = self.tasks.get(configuration.id)
        if task is None or task.done():
            self.tasks[configuration.id] = self.driver.create_task(
                self._reconnect(configuration), name=f"dingtalk:{configuration.id}"
            )

    async def _reconnect(self, configuration: BotConnectionConfiguration) -> None:
        driver = self.driver
        if not isinstance(driver, WebSocketClient):
            raise OpsMeshError(
                "CONFIG_INVALID", "DingTalk Stream requires a WebSocket client Driver"
            )
        bot = self.get_bot(configuration)
        delay = bot.api_client.settings.reconnect_seconds
        while True:
            try:
                credentials = await bot.api_client.open_stream()
                endpoint, ticket = credentials.get("endpoint"), credentials.get("ticket")
                if not isinstance(endpoint, str) or not isinstance(ticket, str) or not ticket:
                    raise OpsMeshError(
                        "BOT_PROTOCOL_ERROR", "DingTalk did not return a Stream ticket"
                    )
                try:
                    endpoint = trusted_endpoint(endpoint, websocket=True)
                except ValueError as error:
                    raise OpsMeshError(
                        "BOT_PROTOCOL_ERROR", "DingTalk returned an invalid Stream endpoint"
                    ) from error
                async with driver.websocket(
                    endpoint + "?" + urlencode({"ticket": ticket})
                ) as socket:
                    session = DingTalkStreamSession(
                        socket, configuration, bot.api_client.settings, self.emit
                    )
                    self.sessions[configuration.id] = session
                    self.bots[configuration.id] = bot
                    delay = bot.api_client.settings.reconnect_seconds
                    try:
                        await session.run()
                    finally:
                        self.sessions.pop(configuration.id, None)
                        if self.bots.get(configuration.id) is bot:
                            self.bots.pop(configuration.id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Ticket-bearing URLs and HTTP client exceptions must not enter logs.
                logger.warning("DingTalk Stream disconnected", extra={"bot_id": configuration.id})
            await asyncio.sleep(delay)
            delay = min(60, delay * 2)

    async def disconnect_account(self, account_id: str) -> None:
        task = self.tasks.pop(account_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        session = self.sessions.pop(account_id, None)
        if session is not None:
            session.closed = True
            await session.socket.close(1001)
        bot = self._clients.pop(account_id, None)
        if bot is not None:
            bot.api_client.clear()
            if self.bots.get(account_id) is bot:
                self.bots.pop(account_id)

    def connection_status(
        self, configuration: BotConnectionConfiguration
    ) -> BotAdapterConnectionStatus:
        session = self.sessions.get(configuration.id)
        task = self.tasks.get(configuration.id)
        state: Literal["disabled", "connected", "connecting", "configured", "disconnected"] = (
            "disabled"
            if not configuration.enabled
            else "connected"
            if session is not None and not session.closed
            else "connecting"
            if task is not None and not task.done()
            else "configured"
            if not self.autostart
            else "disconnected"
        )
        return BotAdapterConnectionStatus(transport="stream", state=state)

    async def close(self) -> None:
        for account_id in set(self.tasks) | set(self.sessions) | set(self._clients):
            await self.disconnect_account(account_id)
