"""Own native QQ SDK clients and their shared HTTP transport."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from qqbot_agent_sdk import QQApiClient

from ..operations import OperationError, operation_response
from .config import QQSettings
from .errors import QQSendError

if TYPE_CHECKING:
    from .channel import QQChannel


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
    """Keep one native API client and token cache per declared connection."""

    def __init__(
        self, settings: QQSettings, http: httpx.AsyncClient | None
    ) -> None:
        """Borrow HTTP transport and own per-connection native API clients."""
        self.settings = settings
        self._http = http
        self._owns_http = http is None
        self._accounts: dict[str, QQApiClient] = {}

    def open(self) -> None:
        """Open the shared HTTP transport on adapter startup."""
        if self._http is None:
            self._http = httpx.AsyncClient(trust_env=False)

    def account(self, channel: QQChannel, secret: str) -> QQApiClient:
        """Return the SDK client bound to this connection's credentials."""
        if self._http is None:
            raise QQSendError("The QQ HTTP client is not open")
        client = self._accounts.get(channel.self_id)
        if client is None:
            client = QQApiClient(
                app_id=channel.settings.app_id,
                client_secret=secret,
                log_tag=channel.self_id,
            )
            client.setup(
                _APITransport(self._http, self.settings.request_timeout)
            )
            self._accounts[channel.self_id] = client
        return client

    def forget(self, connection_id: str) -> None:
        """Release one account after its gateway has stopped."""
        client = self._accounts.pop(connection_id, None)
        if client is not None:
            client.clear_token()

    async def close(self) -> None:
        """Discard SDK clients and close the owned HTTP transport."""
        for connection_id in tuple(self._accounts):
            self.forget(connection_id)
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None
