"""Official QQ protocol adapter, independent of platform accounts and persistence."""

import asyncio
import json
from collections.abc import Callable
from typing import Literal

import httpx
from pydantic import ValidationError

from opsmesh.framework.adapters import (
    BotAdapterConnectionStatus,
    BotAdapterDescriptor,
    BotConnectionConfiguration,
    BotWebhookRequest,
    BotWebhookResponse,
    BotWebhookResult,
    OpsMeshBotAdapter,
)
from opsmesh.framework.drivers import DriverState, OpsMeshDriver, WebSocketClient
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.qq.authentication import QQWebhookAuthenticator
from opsmesh.infrastructure.bots.qq.bot import QQBot
from opsmesh.infrastructure.bots.qq.client import QQAPIClient
from opsmesh.infrastructure.bots.qq.configuration import QQBotSettings
from opsmesh.infrastructure.bots.qq.event import decode_event
from opsmesh.infrastructure.bots.qq.transport import QQGatewayConnection


class QQAdapter(OpsMeshBotAdapter):
    name = "qq"

    def __init__(
        self,
        driver: OpsMeshDriver,
        client: httpx.AsyncClient,
        resolve_secret: Callable[[str], str],
        *,
        autostart: bool = True,
    ) -> None:
        super().__init__(driver, client, resolve_secret, autostart=autostart)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.connections: dict[str, QQGatewayConnection] = {}

    def describe(self) -> BotAdapterDescriptor:
        return BotAdapterDescriptor(
            name=self.name,
            display_name="QQ 官方机器人",
            capabilities=frozenset(
                {
                    "webhook",
                    "websocket",
                    "signed_events",
                    "text",
                    "markdown",
                    "group_media",
                    "c2c_media",
                    "passive_reply",
                    "c2c",
                    "group",
                    "channel",
                    "dms",
                    "full_group_messages",
                    "proactive_group_messages",
                    "group_message_recall",
                    "group_state",
                }
            ),
            settings_schema=QQBotSettings.model_json_schema(),
            credential_names=frozenset({"client_secret"}),
        )

    def validate_configuration(self, configuration: BotConnectionConfiguration) -> None:
        if configuration.adapter != self.name:
            raise OpsMeshError("CONFIG_INVALID", "QQ configuration belongs to another adapter")
        if set(configuration.credentials) != {"client_secret"}:
            raise OpsMeshError("CONFIG_INVALID", "QQ requires exactly one client_secret reference")
        try:
            settings = QQBotSettings.model_validate(configuration.settings)
        except ValidationError as error:
            raise OpsMeshError("CONFIG_INVALID", "Invalid QQ bot settings") from error
        if (
            settings.transport == "websocket"
            and self.autostart
            and not isinstance(self.driver, WebSocketClient)
        ):
            raise OpsMeshError("CONFIG_INVALID", "QQ Gateway requires a WebSocket client Driver")

    def get_bot(self, configuration: BotConnectionConfiguration) -> QQBot:
        self.validate_configuration(configuration)
        if not configuration.enabled:
            raise OpsMeshError("BOT_DISABLED", "QQ bot is disabled")
        existing = self.bots.get(configuration.id)
        if isinstance(existing, QQBot) and existing.configuration == configuration:
            return existing
        if isinstance(existing, QQBot):
            existing.client.clear_token()
        settings = QQBotSettings.model_validate(configuration.settings)
        client = QQAPIClient(
            self.client,
            settings,
            lambda: self.resolve_secret(configuration.credentials["client_secret"]),
        )
        bot = QQBot(self, configuration, client)
        self.bots[configuration.id] = bot
        return bot

    def configure(self, configuration: BotConnectionConfiguration) -> None:
        self.validate_configuration(configuration)
        existing_connection = self.connections.get(configuration.id)
        previous_task = self.tasks.get(configuration.id)
        settings = QQBotSettings.model_validate(configuration.settings)
        needs_gateway = configuration.enabled and settings.transport == "websocket"
        if existing_connection is not None and (
            not needs_gateway or existing_connection.bot.configuration != configuration
        ):
            if previous_task is not None:
                previous_task.cancel()
            self.connections.pop(configuration.id, None)
        if not configuration.enabled:
            existing = self.bots.pop(configuration.id, None)
            if isinstance(existing, QQBot):
                existing.client.clear_token()
            return
        bot = self.get_bot(configuration)
        if (
            not needs_gateway
            or not self.autostart
            or self.driver.state not in {DriverState.STARTING, DriverState.RUNNING}
        ):
            return
        if (
            existing_connection is not None
            and existing_connection.bot.configuration == configuration
            and previous_task is not None
            and not previous_task.done()
        ):
            return
        driver = self.driver
        if not isinstance(driver, WebSocketClient):
            raise OpsMeshError("CONFIG_INVALID", "QQ Gateway requires a WebSocket client Driver")
        connection = QQGatewayConnection(driver, bot, self.emit)
        self.connections[configuration.id] = connection
        self.tasks[configuration.id] = self.driver.create_task(
            connection.run(previous_task), name=f"qq:{configuration.id}"
        )

    async def disconnect_account(self, account_id: str) -> None:
        task = self.tasks.pop(account_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.connections.pop(account_id, None)
        existing = self.bots.pop(account_id, None)
        if isinstance(existing, QQBot):
            existing.client.clear_token()

    def connection_status(
        self, configuration: BotConnectionConfiguration
    ) -> BotAdapterConnectionStatus:
        settings = QQBotSettings.model_validate(configuration.settings)
        connection, task = self.connections.get(configuration.id), self.tasks.get(configuration.id)
        state: Literal["disabled", "connected", "connecting", "configured", "disconnected"] = (
            "disabled"
            if not configuration.enabled
            else "configured"
            if settings.transport == "webhook" or not self.autostart
            else "connected"
            if connection is not None and connection.connected
            else "connecting"
            if task is not None and not task.done()
            else "disconnected"
        )
        return BotAdapterConnectionStatus(transport=settings.transport, state=state)

    async def receive_http(
        self, configuration: BotConnectionConfiguration, request: BotWebhookRequest
    ) -> BotWebhookResult:
        self.validate_configuration(configuration)
        if not configuration.enabled:
            raise OpsMeshError("BOT_DISABLED", "QQ bot is disabled")
        if len(request.body) > 1024 * 1024:
            raise OpsMeshError("INPUT_TOO_LARGE", "QQ event exceeds the 1 MiB input limit")
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeError) as error:
            raise OpsMeshError("INVALID_REQUEST", "Invalid QQ webhook JSON") from error
        if not isinstance(payload, dict):
            raise OpsMeshError("INVALID_REQUEST", "QQ webhook must be a JSON object")
        settings = QQBotSettings.model_validate(configuration.settings)
        if settings.transport != "webhook":
            raise OpsMeshError("UNSUPPORTED_FEATURE", "QQ account uses Gateway event delivery")
        app_id = request.header("X-Bot-Appid")
        if app_id is not None and app_id != settings.app_id:
            raise OpsMeshError("UNAUTHORIZED", "QQ callback belongs to another application")
        authenticator = QQWebhookAuthenticator(
            self.resolve_secret(configuration.credentials["client_secret"]),
            max_age_seconds=settings.signature_max_age_seconds,
        )
        if payload.get("op") == 13:
            if app_id != settings.app_id or not isinstance(payload.get("d"), dict):
                raise OpsMeshError("UNAUTHORIZED", "QQ verification request is invalid")
            return BotWebhookResult(
                response=BotWebhookResponse(body=authenticator.challenge(payload["d"]))
            )
        authenticator.verify(request)
        if payload.get("op") != 0:
            raise OpsMeshError("INVALID_REQUEST", "QQ webhook opcode is unsupported")
        return BotWebhookResult(
            incoming=decode_event(
                payload,
                account_id=configuration.id,
                self_id=settings.event_self_id(payload, configuration.self_id),
            ),
            response=BotWebhookResponse(body={"op": 12}),
        )

    async def close(self) -> None:
        for identifier in set(self.bots) | set(self.tasks) | set(self.connections):
            await self.disconnect_account(identifier)
