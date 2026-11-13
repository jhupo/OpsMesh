"""Cancellable DingTalk API requests and response validation."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from pydantic import JsonValue
from yarl import URL

from ..attachments import Attachment
from ..operations import OperationError, operation_response
from .config import DingTalkConnectionSettings, DingTalkSettings
from .errors import DingTalkSendError

_MAX_RESPONSE_BYTES = 1024 * 1024


class DingTalkClient:
    """Own HTTP requests for Stream admission, media and robot replies."""

    def __init__(
        self, settings: DingTalkSettings, http: httpx.AsyncClient | None
    ) -> None:
        """Keep injectable transport ownership explicit."""
        self.settings = settings
        self._http = http
        self._owns_http = http is None

    def open(self) -> None:
        """Open the HTTP transport on adapter startup."""
        if self._http is None:
            self._http = httpx.AsyncClient(trust_env=False)

    async def close(self) -> None:
        """Close the owned HTTP transport."""
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
        except httpx.HTTPError as error:
            raise DingTalkSendError("DingTalk reply request failed") from error
        return self._response(response)

    async def access_token(self, connection: DingTalkConnectionSettings) -> str:
        """Get a token for the owning bot without a blocking SDK request."""
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
            raise DingTalkSendError("DingTalk media upload failed") from None
        result = self._response(response)
        media_id = result.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise DingTalkSendError(
                "DingTalk upload response is missing media_id"
            )
        return media_id

    @staticmethod
    def _response(response: httpx.Response) -> dict[str, Any]:
        if not 200 <= response.status_code < 300:
            raise DingTalkSendError(
                f"DingTalk reply failed with status {response.status_code}"
            )
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise DingTalkSendError("DingTalk response is too large")
        try:
            data = response.json()
        except ValueError as error:
            raise DingTalkSendError("DingTalk response is not JSON") from error
        if not isinstance(data, dict):
            raise DingTalkSendError("DingTalk response is invalid")
        if data.get("errcode", 0) not in (0, None) or data.get("code") not in (
            None,
            0,
            "0",
        ):
            code = str(data.get("errcode") or data.get("code"))
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
                code = "unknown"
            raise DingTalkSendError(
                f"DingTalk rejected the request with code {code}"
            )
        return data
