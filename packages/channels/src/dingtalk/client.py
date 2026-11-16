"""Cancellable DingTalk API requests and response validation."""

from __future__ import annotations

import os
import time
from typing import Any

import anyio
import httpx
from pydantic import JsonValue
from yarl import URL

from ..attachments import Attachment
from ..operations import OperationError, operation_response
from .config import DingTalkConnectionSettings, DingTalkSettings
from .errors import DingTalkSendError

_TOKEN_REFRESH_MARGIN = 60


class DingTalkClient:
    """Own HTTP requests for Stream admission, media and robot replies."""

    def __init__(
        self, settings: DingTalkSettings, http: httpx.AsyncClient | None
    ) -> None:
        """Keep injectable transport ownership explicit."""
        self.settings = settings
        self._http = http
        self._owns_http = http is None
        self._tokens: dict[tuple[str, str], tuple[str, float]] = {}
        self._token_locks: dict[tuple[str, str], anyio.Lock] = {}

    def open(self) -> None:
        """Open the HTTP transport on adapter startup."""
        if self._http is None:
            self._http = httpx.AsyncClient(trust_env=False)

    async def close(self) -> None:
        """Close the owned HTTP transport."""
        self._tokens.clear()
        self._token_locks.clear()
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call an official endpoint and validate its JSON object response."""
        if self._http is None:
            raise DingTalkSendError("The DingTalk HTTP client is not open")
        parsed = URL(url)
        if parsed.scheme != "https" or not parsed.host:
            raise DingTalkSendError("The DingTalk reply webhook is invalid")
        try:
            response = await self._http.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.settings.request_timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise OperationError(0, "transport_error") from None
        return self._response(response)

    def forget(self, connection: DingTalkConnectionSettings) -> None:
        """Discard cached credentials when their owning connection stops."""
        key = (connection.client_id, connection.client_secret_ref)
        self._tokens.pop(key, None)
        self._token_locks.pop(key, None)

    async def access_token(self, connection: DingTalkConnectionSettings) -> str:
        """Reuse a connection token and coalesce concurrent refreshes."""
        key = (connection.client_id, connection.client_secret_ref)
        lock = self._token_locks.setdefault(key, anyio.Lock())
        async with lock:
            cached = self._tokens.get(key)
            if cached is not None and time.monotonic() < cached[1]:
                return cached[0]
            secret = os.environ.get(connection.client_secret_ref)
            if not secret:
                raise DingTalkSendError("DingTalk credentials are unavailable")
            result = await self.post_json(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                {"appKey": connection.client_id, "appSecret": secret},
            )
            token = result.get("accessToken")
            if not isinstance(token, str) or not token:
                raise DingTalkSendError("DingTalk authentication failed")
            lifetime = result.get("expireIn")
            if isinstance(lifetime, (int, float)) and lifetime > 0:
                remaining = max(
                    0, lifetime - min(_TOKEN_REFRESH_MARGIN, lifetime / 10)
                )
                self._tokens[key] = (token, time.monotonic() + remaining)
            return token

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str,
        body: dict[str, JsonValue] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> JsonValue:
        """Use the account transport for fixed official operation paths."""
        if self._http is None:
            raise DingTalkSendError("The DingTalk HTTP client is not open")
        try:
            response = await self._http.request(
                method,
                "https://api.dingtalk.com" + path,
                json=body,
                params=params,
                headers={"x-acs-dingtalk-access-token": token},
                timeout=self.settings.request_timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise OperationError(0, "transport_error") from None
        return operation_response(response)

    async def upload_media(
        self,
        token: str,
        attachment: Attachment,
        content: bytes,
        *,
        kind: str,
    ) -> str:
        """Use the SDK's multipart upload protocol with cancellable I/O."""
        if self._http is None:
            raise DingTalkSendError("The DingTalk HTTP client is not open")
        try:
            response = await self._http.post(
                "https://oapi.dingtalk.com/media/upload",
                params={"access_token": token},
                data={"type": kind},
                files={
                    "media": (attachment.name, content, attachment.media_type)
                },
                timeout=self.settings.request_timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise OperationError(0, "transport_error") from None
        result = self._response(response)
        media_id = result.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise DingTalkSendError(
                "DingTalk upload response is missing media_id"
            )
        return media_id

    @staticmethod
    def _response(response: httpx.Response) -> dict[str, Any]:
        data = operation_response(response)
        if not isinstance(data, dict):
            raise OperationError(response.status_code, "invalid_response")
        return data
