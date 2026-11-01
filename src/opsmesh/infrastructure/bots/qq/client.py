"""Cached QQ application tokens and explicit delivery-outcome classification."""

import asyncio
import time
from collections.abc import Callable

import httpx

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.bots.qq.configuration import QQBotSettings


def _json_object(response: httpx.Response, *, delivery: bool) -> JsonData:
    try:
        payload = response.json()
    except ValueError as error:
        raise OpsMeshError(
            "DELIVERY_UNKNOWN" if delivery else "BOT_UNAVAILABLE", "QQ returned invalid JSON"
        ) from error
    if not isinstance(payload, dict):
        raise OpsMeshError(
            "DELIVERY_UNKNOWN" if delivery else "BOT_UNAVAILABLE", "QQ returned an invalid object"
        )
    return payload


def _provider_code(payload: JsonData) -> str | None:
    code = payload.get("code")
    return str(code)[:64] if isinstance(code, (str, int)) else None


class QQAPIClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: QQBotSettings,
        resolve_secret: Callable[[], str],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client, self.settings, self._resolve_secret = client, settings, resolve_secret
        self._clock = clock
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    def clear_token(self) -> None:
        self._access_token, self._expires_at = None, 0.0

    async def _token(self) -> str:
        async with self._token_lock:
            if self._access_token is not None and self._clock() < self._expires_at:
                return self._access_token
            try:
                response = await self.client.post(
                    self.settings.token_endpoint,
                    json={"appId": self.settings.app_id, "clientSecret": self._resolve_secret()},
                    timeout=self.settings.request_timeout_seconds,
                    follow_redirects=False,
                )
            except httpx.RequestError as error:
                raise OpsMeshError(
                    "BOT_UNAVAILABLE", "QQ token endpoint is unavailable", retryable=True
                ) from error
            if response.status_code in {401, 403}:
                raise OpsMeshError(
                    "BOT_AUTHENTICATION_FAILED", "QQ rejected the application credential"
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise OpsMeshError(
                    "BOT_UNAVAILABLE", "QQ token endpoint rejected the request", retryable=True
                )
            if not 200 <= response.status_code < 300:
                raise OpsMeshError("BOT_AUTHENTICATION_FAILED", "QQ token request was rejected")
            payload = _json_object(response, delivery=False)
            token = payload.get("access_token")
            try:
                lifetime = float(payload.get("expires_in", 0))
            except (ValueError, TypeError):
                lifetime = 0
            if not isinstance(token, str) or not token or not 0 < lifetime <= 86400:
                raise OpsMeshError(
                    "BOT_AUTHENTICATION_FAILED", "QQ returned an invalid access token"
                )
            self._access_token = token
            self._expires_at = self._clock() + lifetime - min(60, lifetime / 2)
            return token

    async def post(self, path: str, data: JsonData) -> JsonData:
        token = await self._token()
        try:
            response = await self.client.post(
                self.settings.api_base_url + path,
                json=data,
                headers={"Authorization": f"QQBot {token}"},
                timeout=self.settings.request_timeout_seconds,
                follow_redirects=False,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            raise OpsMeshError(
                "BOT_UNAVAILABLE", "Unable to connect to QQ", retryable=True
            ) from error
        except httpx.RequestError as error:
            raise OpsMeshError("DELIVERY_UNKNOWN", "QQ request outcome is uncertain") from error
        if response.status_code in {401, 403}:
            self.clear_token()
            raise OpsMeshError("BOT_AUTHENTICATION_FAILED", "QQ rejected the request credential")
        if response.status_code == 429:
            raise OpsMeshError("BOT_RATE_LIMITED", "QQ rate limit reached", retryable=True)
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            raise OpsMeshError("DELIVERY_UNKNOWN", "QQ returned an uncertain delivery outcome")
        payload = _json_object(response, delivery=True)
        code = _provider_code(payload)
        if response.status_code >= 400 or code not in {None, "0"}:
            raise OpsMeshError(
                "BOT_REQUEST_REJECTED", "QQ rejected the message", provider="qq", provider_code=code
            )
        return payload
