"""OneBot HTTP API client with explicit ambiguous-delivery errors.

https://github.com/botuniverse/onebot-11/blob/master/api/public.md
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import httpx

from opsmesh.framework.adapters import (
    BotConnectionConfiguration,
    BotRef,
    OpsMeshBot,
    OpsMeshEvent,
    OpsMeshMessage,
    ReplyTarget,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.onebot.configuration import OneBotSettings

if TYPE_CHECKING:
    from opsmesh.infrastructure.bots.onebot.adapter import OneBotAdapter
from opsmesh.infrastructure.bots.onebot.event import OneBotMessageEvent
from opsmesh.infrastructure.bots.onebot.message import encode_message
from opsmesh.infrastructure.bots.onebot.response import api_result


class OneBot(OpsMeshBot):
    def __init__(
        self,
        adapter: OneBotAdapter,
        account: BotConnectionConfiguration,
        client: httpx.AsyncClient,
        access_token: str | None = None,
        api_caller: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> None:
        super().__init__(adapter, account.self_id)
        self.account, self.client, self._access_token = account, client, access_token
        self.settings = OneBotSettings.model_validate(account.settings)
        self._api_caller = api_caller

    @property
    def ref(self) -> BotRef:
        return BotRef(adapter_id=self.type, bot_id=self.account.id)

    async def send(self, target: OpsMeshEvent | ReplyTarget, message: OpsMeshMessage) -> Any:
        if isinstance(target, OneBotMessageEvent):
            channel = target.channel_id
        elif isinstance(target, ReplyTarget):
            if target.bot != self.ref:
                raise OpsMeshError("INVALID_REQUEST", "Reply target belongs to another bot")
            if target.thread_id:
                raise OpsMeshError(
                    "UNSUPPORTED_FEATURE", "OneBot v11 has no thread reply capability"
                )
            channel = target.channel_id
        else:
            raise OpsMeshError("INVALID_REQUEST", "OneBot needs an explicit conversation target")
        kind, separator, identifier = channel.partition(":")
        if not separator or kind not in {"private", "group"} or not identifier.isdecimal():
            raise OpsMeshError(
                "INVALID_REQUEST", "OneBot target must be private:<id> or group:<id>"
            )
        encoded = encode_message(message)
        data = await self.call_api(
            "send_msg",
            message_type=kind,
            **{f"{'user' if kind == 'private' else 'group'}_id": int(identifier)},
            message=encoded,
        )
        if not isinstance(data, dict) or data.get("message_id") is None:
            raise OpsMeshError("DELIVERY_UNKNOWN", "OneBot did not return a message identity")
        return str(data["message_id"])

    async def upload_file(self, target: ReplyTarget, name: str, content: bytes) -> str | None:
        if target.bot != self.ref or target.thread_id:
            raise OpsMeshError("INVALID_REQUEST", "File target is incompatible with the bot")
        if self.settings.file_upload != "napcat":
            raise OpsMeshError("UNSUPPORTED_FEATURE", "This account has no file upload extension")
        kind, separator, identifier = target.channel_id.partition(":")
        if not separator or kind not in {"private", "group"} or not identifier.isdecimal():
            raise OpsMeshError("INVALID_REQUEST", "File target must be private:<id> or group:<id>")
        response = await self.call_api(
            f"upload_{kind}_file",
            name=name,
            file="base64://" + base64.b64encode(content).decode(),
            **{f"{'user' if kind == 'private' else 'group'}_id": identifier},
        )
        return (
            str(response["file_id"])
            if isinstance(response, dict) and response.get("file_id")
            else None
        )

    async def call_api(self, api: str, **data: Any) -> Any:
        if not api or any(c not in "abcdefghijklmnopqrstuvwxyz_0123456789" for c in api):
            raise OpsMeshError("INVALID_REQUEST", "Invalid OneBot API name")
        if self._api_caller is not None:
            return await self._api_caller(api, data)
        headers = {"Authorization": f"Bearer {self._access_token}"} if self._access_token else {}
        try:
            response = await self.client.post(
                f"{self.settings.api_base_url}/{api}", json=data, headers=headers, timeout=30
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise OpsMeshError(
                "BOT_UNAVAILABLE", "Unable to connect to OneBot", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise OpsMeshError("DELIVERY_UNKNOWN", "OneBot request outcome is uncertain") from exc
        if response.status_code == 429:
            raise OpsMeshError("BOT_RATE_LIMITED", "OneBot rate limit reached", retryable=True)
        if response.status_code in {401, 403}:
            raise OpsMeshError(
                "BOT_AUTHENTICATION_FAILED", "OneBot rejected the configured credential"
            )
        if response.status_code >= 400:
            raise OpsMeshError(
                "DELIVERY_UNKNOWN", "OneBot returned an HTTP error with uncertain outcome"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpsMeshError("DELIVERY_UNKNOWN", "OneBot returned an invalid response") from exc
        return api_result(payload)
