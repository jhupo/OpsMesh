"""Own one QQ connection's native API client and HTTP transport."""

from __future__ import annotations

from typing import Any

import httpx
from qqbot_agent_sdk import QQApiClient

from ..operations import OperationError, operation_response
from .config import QQConnectionSettings


class _APITransport:
    """Apply framework response limits before the SDK consumes responses."""

    def __init__(self, http: httpx.AsyncClient, timeout: float) -> None:
        self.http = http
        self.timeout = timeout

    async def request(
        self, method: str, url: str, **options: Any
    ) -> httpx.Response:
        """Preserve safe vendor error types without logging response bodies."""
        options["timeout"] = self.timeout
        try:
            response = await self.http.request(
                method, url, follow_redirects=False, **options
            )
        except httpx.HTTPError:
            raise OperationError(0, "transport_error") from None
        operation_response(response)
        return response


class QQClient:
    """Keep one native API client and token cache for a connection."""

    def __init__(
        self,
        settings: QQConnectionSettings,
        secret: str,
        *,
        name: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """Bind the account to its credentials and transport."""
        self.settings = settings
        self._http = http or httpx.AsyncClient(trust_env=False)
        self._owns_http = http is None
        self.api = QQApiClient(
            app_id=settings.app_id,
            client_secret=secret,
            log_tag=name,
        )
        self.api.setup(_APITransport(self._http, settings.request_timeout))

    async def close(self) -> None:
        """Discard the account token and close an owned transport."""
        self.api.clear_token()
        if self._owns_http:
            await self._http.aclose()
