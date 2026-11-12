"""DingTalk webhook HTTP requests and response validation."""

from __future__ import annotations

from typing import Any

import httpx
from yarl import URL

from .config import DingTalkSettings
from .errors import DingTalkSendError

_MAX_RESPONSE_BYTES = 1024 * 1024


class DingTalkClient:
    """Own the HTTP transport used for session webhook replies."""

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
        self, url: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a webhook reply and validate its JSON object response."""
        if self._http is None:
            raise DingTalkSendError("The DingTalk HTTP client is not open")
        parsed = URL(url)
        if parsed.scheme != "https" or not parsed.host:
            raise DingTalkSendError("The DingTalk reply webhook is invalid")
        try:
            response = await self._http.post(
                url,
                json=payload,
                timeout=self.settings.request_timeout,
            )
        except httpx.HTTPError as error:
            raise DingTalkSendError("DingTalk reply request failed") from error
        if response.status_code >= 400:
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
        return data
