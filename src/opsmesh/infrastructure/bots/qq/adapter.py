"""Official QQ protocol adapter, independent of platform accounts and persistence."""

import json

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
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.qq.authentication import QQWebhookAuthenticator
from opsmesh.infrastructure.bots.qq.bot import QQBot
from opsmesh.infrastructure.bots.qq.client import QQAPIClient
from opsmesh.infrastructure.bots.qq.configuration import QQBotSettings
from opsmesh.infrastructure.bots.qq.event import decode_event


class QQAdapter(OpsMeshBotAdapter):
    name = "qq"

    def describe(self) -> BotAdapterDescriptor:
        return BotAdapterDescriptor(
            name=self.name,
            display_name="QQ 官方机器人",
            capabilities=frozenset(
                {
                    "webhook",
                    "signed_events",
                    "text",
                    "markdown",
                    "passive_reply",
                    "c2c",
                    "group",
                    "channel",
                    "dms",
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
            QQBotSettings.model_validate(configuration.settings)
        except ValidationError as error:
            raise OpsMeshError("CONFIG_INVALID", "Invalid QQ bot settings") from error

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
        if configuration.enabled:
            self.get_bot(configuration)
        else:
            existing = self.bots.pop(configuration.id, None)
            if isinstance(existing, QQBot):
                existing.client.clear_token()

    async def disconnect_account(self, account_id: str) -> None:
        existing = self.bots.pop(account_id, None)
        if isinstance(existing, QQBot):
            existing.client.clear_token()

    def connection_status(
        self, configuration: BotConnectionConfiguration
    ) -> BotAdapterConnectionStatus:
        return BotAdapterConnectionStatus(
            transport="webhook", state="configured" if configuration.enabled else "disabled"
        )

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
                payload, account_id=configuration.id, self_id=configuration.self_id
            ),
            response=BotWebhookResponse(body={"op": 12}),
        )

    async def close(self) -> None:
        for identifier in tuple(self.bots):
            await self.disconnect_account(identifier)
