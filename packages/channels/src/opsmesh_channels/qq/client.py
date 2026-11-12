"""QQ HTTP requests and per-connection access-token caching."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anyio
import httpx

from .config import QQSettings
from .errors import QQSendError

_MAX_RESPONSE_BYTES = 1024 * 1024
_TOKEN_SAFETY_SECONDS = 60


@dataclass
class _Token:
    value: str
    expires_at: float


class QQClient:
    """Own the vendor HTTP connection and serialize token refreshes."""

    def __init__(
        self, settings: QQSettings, http: httpx.AsyncClient | None
    ) -> None:
        """Keep injectable transport ownership explicit."""
        self.settings = settings
        self._http = http
        self._owns_http = http is None
        self._tokens: dict[str, _Token] = {}
        self._refresh_locks: dict[str, anyio.Lock] = {}

    def open(self) -> None:
        """Open the HTTP transport on adapter startup."""
        if self._http is None:
            self._http = httpx.AsyncClient(trust_env=False)

    async def close(self) -> None:
        """Release credentials and owned network resources."""
        self._tokens.clear()
        self._refresh_locks.clear()
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def forget(self, connection_id: str) -> None:
        """Discard the cached token for a removed connection."""
        self._tokens.pop(connection_id, None)
        self._refresh_locks.pop(connection_id, None)

    async def access_token(
        self, connection_id: str, app_id: str, secret: str
    ) -> str:
        """Share one token refresh among concurrent sends."""
        lock = self._refresh_locks.setdefault(connection_id, anyio.Lock())
        async with lock:
            cached = self._tokens.get(connection_id)
            if cached is not None and time.monotonic() < cached.expires_at:
                return cached.value
            data = await self.post_json(
                self.settings.token_url,
                {"appId": app_id, "clientSecret": secret},
            )
            token = data.get("access_token")
            if not isinstance(token, str) or not token:
                raise QQSendError("QQ token response is invalid")
            try:
                lifetime = int(data.get("expires_in", 7200))
            except (TypeError, ValueError):
                lifetime = 7200
            self._tokens[connection_id] = _Token(
                value=token,
                expires_at=time.monotonic()
                + max(lifetime - _TOKEN_SAFETY_SECONDS, 1),
            )
            return token

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send a vendor request and validate its JSON object response."""
        if self._http is None:
            raise QQSendError("The QQ HTTP client is not open")
        try:
            response = await self._http.post(
                url,
                json=payload,
                headers=headers or {},
                timeout=self.settings.request_timeout,
            )
        except httpx.HTTPError as error:
            raise QQSendError("QQ vendor request failed") from error
        if response.status_code >= 400:
            raise QQSendError(
                f"QQ vendor request failed with status {response.status_code}"
            )
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise QQSendError("QQ vendor response is too large")
        try:
            data = response.json()
        except ValueError as error:
            raise QQSendError("QQ vendor response is not JSON") from error
        if not isinstance(data, dict):
            raise QQSendError("QQ vendor response is invalid")
        return data
