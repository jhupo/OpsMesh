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

    async def access_token(self) -> str:
        """Provide an in-memory credential for the authenticated QQ Gateway handshake."""
        return await self._token()

    async def get(self, path: str) -> JsonData:
        return await self._request("GET", path)

    async def delete(self, path: str) -> JsonData:
        return await self._request("DELETE", path)

    async def post(self, path: str, data: JsonData) -> JsonData:
        return await self._request("POST", path, data)

    async def _request(self, method: str, path: str, data: JsonData | None = None) -> JsonData:
        token = await self._token()
        try:
            response = await self.client.request(
                method,
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
            raise OpsMeshError(
                "BOT_UNAVAILABLE" if method == "GET" else "DELIVERY_UNKNOWN",
                "QQ request outcome is uncertain",
                retryable=method == "GET",
            ) from error
        try:
            raw_payload = response.json()
        except ValueError:
            raw_payload = None
        code = _provider_code(raw_payload) if isinstance(raw_payload, dict) else None
        if code in {"40062003", "11253"}:
            raise OpsMeshError(
                "BOT_PERMISSION_DENIED",
                "QQ denied the operation permission",
                provider="qq",
                provider_code=code,
            )
        if code == "40064004":
            raise OpsMeshError(
                "BOT_MESSAGE_EXPIRED",
                "QQ message recall window has expired",
                provider="qq",
                provider_code=code,
            )
        if response.status_code == 401:
            self.clear_token()
            raise OpsMeshError(
                "BOT_AUTHENTICATION_FAILED",
                "QQ rejected the request credential",
                provider="qq",
                provider_code=code,
            )
        if response.status_code == 403:
            raise OpsMeshError(
                "BOT_PERMISSION_DENIED",
                "QQ denied the operation permission",
                provider="qq",
                provider_code=code,
            )
        if response.status_code == 429 or code == "22009":
            raise OpsMeshError(
                "BOT_RATE_LIMITED",
                "QQ rate limit reached",
                retryable=True,
                provider="qq",
                provider_code=code,
            )
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            raise OpsMeshError(
                "BOT_UNAVAILABLE" if method == "GET" else "DELIVERY_UNKNOWN",
                "QQ returned an uncertain operation outcome",
                retryable=method == "GET",
                provider="qq",
                provider_code=code,
            )
        if method == "DELETE" and response.status_code in {200, 204} and not response.content:
            return {}
        payload = _json_object(response, delivery=method != "GET")
        if response.status_code >= 400 or code not in {None, "0"}:
            raise OpsMeshError(
                "BOT_REQUEST_REJECTED",
                "QQ rejected the operation",
                provider="qq",
                provider_code=code,
            )
        if method == "DELETE" and response.status_code not in {200, 204}:
            raise OpsMeshError("DELIVERY_UNKNOWN", "QQ did not confirm message recall")
        return payload
