"""Optional OneBot v11 transport, independent of platform account ownership."""

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from opsmesh.framework.adapters import (
    BotAdapterConnectionStatus,
    BotAdapterDescriptor,
    BotConnectionConfiguration,
    BotInboundEvent,
    BotWebhookRequest,
    BotWebhookResult,
    OpsMeshBotAdapter,
)
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.onebot.bot import OneBot
from opsmesh.infrastructure.bots.onebot.configuration import OneBotSettings
from opsmesh.infrastructure.bots.onebot.event import decode_event
from opsmesh.infrastructure.bots.onebot.websocket import OneBotConnectionManager


class OneBotAdapter(OpsMeshBotAdapter):
    name = "onebot_v11"

    def __init__(
        self,
        driver: OpsMeshDriver,
        client: httpx.AsyncClient,
        resolve_secret: Callable[[str], str],
        *,
        autostart: bool = True,
    ) -> None:
        super().__init__(driver, client, resolve_secret, autostart=autostart)
        self.configurations: dict[str, BotConnectionConfiguration] = {}
        self.connections = OneBotConnectionManager(
            driver,
            self._accounts,
            self._receive_websocket,
            resolve_secret,
            autostart=autostart,
            on_connect=self._connect_bot,
        )

    def describe(self) -> BotAdapterDescriptor:
        return BotAdapterDescriptor(
            name=self.name,
            display_name="OneBot v11 (compatibility)",
            capabilities=frozenset(
                {"http", "websocket", "reverse_websocket", "text", "napcat_files"}
            ),
            settings_schema=OneBotSettings.model_json_schema(),
            credential_names=frozenset({"access_token", "webhook_secret"}),
        )

    def validate_configuration(self, configuration: BotConnectionConfiguration) -> None:
        if configuration.adapter != self.name or not configuration.self_id.isdecimal():
            raise OpsMeshError(
                "CONFIG_INVALID", "OneBot needs its adapter name and numeric self ID"
            )
        try:
            settings = OneBotSettings.model_validate(configuration.settings)
        except ValidationError as error:
            raise OpsMeshError("CONFIG_INVALID", "Invalid OneBot transport settings") from error
        if set(configuration.credentials) - {"access_token", "webhook_secret"}:
            raise OpsMeshError("CONFIG_INVALID", "Unknown OneBot credential name")
        required = (
            "webhook_secret"
            if settings.transport == "http"
            else "access_token"
            if settings.transport == "reverse_websocket"
            else None
        )
        if required and required not in configuration.credentials:
            raise OpsMeshError(
                "CONFIG_INVALID", f"OneBot requires a {required} credential reference"
            )

    async def _accounts(self) -> list[BotConnectionConfiguration]:
        return list(self.configurations.values())

    def configure(self, configuration: BotConnectionConfiguration) -> None:
        self.validate_configuration(configuration)
        self.configurations[configuration.id] = configuration
        self.connections.connect(configuration)

    def get_bot(self, configuration: BotConnectionConfiguration) -> OneBot:
        self.validate_configuration(configuration)
        settings = OneBotSettings.model_validate(configuration.settings)
        reference = configuration.credentials.get("access_token")
        token = self.resolve_secret(reference) if reference else None

        async def call(action: str, data: dict[str, Any]) -> Any:
            return await self.connections.call(configuration.id, action, data)

        return OneBot(
            self,
            configuration,
            self.client,
            token,
            api_caller=call if settings.transport != "http" else None,
        )

    def _connect_bot(self, configuration: BotConnectionConfiguration) -> Callable[[], None]:
        bot = self.get_bot(configuration)
        if configuration.id in self.bots:
            self.disconnect(configuration.id)
        self.connect(bot)

        def disconnect() -> None:
            if self.bots.get(configuration.id) is bot:
                self.disconnect(configuration.id)

        return disconnect

    async def disconnect_account(self, account_id: str) -> None:
        await self.connections.disconnect(account_id)
        self.configurations.pop(account_id, None)

    def connection_status(
        self, configuration: BotConnectionConfiguration
    ) -> BotAdapterConnectionStatus:
        settings = OneBotSettings.model_validate(configuration.settings)
        session = self.connections.sessions.get(configuration.id)
        task = self.connections.tasks.get(configuration.id)
        return BotAdapterConnectionStatus(
            transport=settings.transport,
            state=(
                "disabled"
                if not configuration.enabled
                else "configured"
                if settings.transport == "http"
                else "connected"
                if session is not None and not session.closed
                else "connecting"
                if task is not None and not task.done()
                else "disconnected"
            ),
            pending_calls=len(session.pending) if session is not None else 0,
        )

    async def receive_http(
        self, configuration: BotConnectionConfiguration, request: BotWebhookRequest
    ) -> BotWebhookResult:
        self.validate_configuration(configuration)
        reference = configuration.credentials.get("webhook_secret")
        if reference is None:
            raise OpsMeshError("UNAUTHORIZED", "HTTP webhooks are not enabled for this account")
        if len(request.body) > 1024 * 1024:
            raise OpsMeshError("INVALID_REQUEST", "Bot event exceeds the 1 MiB input limit")
        expected = (
            "sha1="
            + hmac.new(
                self.resolve_secret(reference).encode(), request.body, hashlib.sha1
            ).hexdigest()
        )
        signature = request.header("x-signature")
        if signature is None or not hmac.compare_digest(expected, signature):
            raise OpsMeshError("UNAUTHORIZED", "Invalid OneBot webhook signature")
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeError) as error:
            raise OpsMeshError("INVALID_REQUEST", "Invalid webhook JSON") from error
        if not isinstance(payload, dict):
            raise OpsMeshError("INVALID_REQUEST", "Webhook JSON must be an object")
        return BotWebhookResult(incoming=self._incoming(configuration, payload))

    @staticmethod
    def _incoming(
        configuration: BotConnectionConfiguration, payload: dict[str, Any]
    ) -> BotInboundEvent:
        event = decode_event(payload, account_id=configuration.id, self_id=configuration.self_id)
        return BotInboundEvent(event, payload, ignored=event.type == "onebot.message_sent")

    async def _receive_websocket(self, account_id: str, payload: dict[str, Any]) -> None:
        configuration = self.configurations.get(account_id)
        if configuration is None or not configuration.enabled:
            raise OpsMeshError("BOT_DISABLED", "OneBot account is not connected")
        await self.emit(configuration, self._incoming(configuration, payload))

    async def close(self) -> None:
        await self.connections.close()
        self.configurations.clear()
        self.bots.clear()
