"""DingTalk token leases and bounded OpenAPI requests with safe error classification."""

import asyncio
import math
import time
from collections.abc import Callable
from typing import Any

import httpx

from opsmesh.framework.adapters import BotConnectionConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.dingtalk.configuration import DingTalkConfiguration


class DingTalkAPIClient:
    def __init__(
        self,
        configuration: BotConnectionConfiguration,
        settings: DingTalkConfiguration,
        client: httpx.AsyncClient,
        resolve_secret: Callable[[str], str],
    ) -> None:
        self.configuration, self.settings = configuration, settings
        self.client, self.resolve_secret = client, resolve_secret
        self.client_id = settings.client_id or configuration.self_id
        self.robot_code = settings.robot_code or configuration.self_id
        self._token = ""
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    def secret(self) -> str:
        return self.resolve_secret(self.configuration.credentials["client_secret"])

    async def _post(
        self, path: str, data: dict[str, Any], *, token: str | None = None, delivery: bool = False
    ) -> dict[str, Any]:
        headers = {"x-acs-dingtalk-access-token": token} if token else {}
        try:
            response = await self.client.post(
                self.settings.api_base_url + path,
                json=data,
                headers=headers,
                timeout=30,
                follow_redirects=False,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            raise OpsMeshError(
                "BOT_UNAVAILABLE", "Unable to connect to DingTalk", retryable=True
            ) from error
        except httpx.RequestError as error:
            code = "DELIVERY_UNKNOWN" if delivery else "BOT_UNAVAILABLE"
            raise OpsMeshError(
                code, "DingTalk request did not complete", retryable=not delivery
            ) from error
        if response.status_code in {401, 403}:
            self._token = ""
            raise OpsMeshError(
                "BOT_AUTHENTICATION_FAILED", "DingTalk rejected the application credential"
            )
        if response.status_code == 429:
            raise OpsMeshError("BOT_RATE_LIMITED", "DingTalk rate limit reached", retryable=True)
        if not 200 <= response.status_code < 300:
            code = (
                "DELIVERY_UNKNOWN"
                if delivery and response.status_code >= 500
                else "BOT_REQUEST_REJECTED"
            )
            raise OpsMeshError(code, "DingTalk rejected the request")
        try:
            result = response.json()
        except ValueError as error:
            raise OpsMeshError(
                "DELIVERY_UNKNOWN" if delivery else "BOT_PROTOCOL_ERROR",
                "Invalid DingTalk response",
            ) from error
        if not isinstance(result, dict):
            raise OpsMeshError("BOT_PROTOCOL_ERROR", "DingTalk response must be an object")
        if result.get("code") or result.get("errcode"):
            raise OpsMeshError("BOT_REQUEST_REJECTED", "DingTalk rejected the operation")
        return result

    async def access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            result = await self._post(
                "/v1.0/oauth2/accessToken", {"appKey": self.client_id, "appSecret": self.secret()}
            )
            token, expires_in = result.get("accessToken"), result.get("expireIn")
            if (
                not isinstance(token, str)
                or not token
                or isinstance(expires_in, bool)
                or not isinstance(expires_in, (int, float))
                or not math.isfinite(expires_in)
                or expires_in <= 0
            ):
                raise OpsMeshError(
                    "BOT_PROTOCOL_ERROR", "DingTalk did not return a valid token lease"
                )
            self._token = token
            self._expires_at = time.monotonic() + expires_in - min(60, expires_in / 10)
            return token

    async def open_stream(self) -> dict[str, Any]:
        return await self._post(
            "/v1.0/gateway/connections/open",
            {
                "clientId": self.client_id,
                "clientSecret": self.secret(),
                "subscriptions": [{"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}],
                "ua": "opsmesh-dingtalk-python/1",
            },
        )

    async def send(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        # An uncertain send is never automatically replayed, including after token refresh.
        return await self._post(path, data, token=await self.access_token(), delivery=True)

    def clear(self) -> None:
        self._token = ""
        self._expires_at = 0
