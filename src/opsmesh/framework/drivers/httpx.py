"""Optional HTTPX driver; the client is created and closed with the driver."""

from __future__ import annotations

from typing import Any

import httpx

from . import HTTPClient, HTTPRequest, HTTPResponse, OpsMeshDriver


class HTTPXDriver(OpsMeshDriver, HTTPClient):
    capabilities = frozenset({"http_client"})

    def __init__(self, **client_options: Any) -> None:
        super().__init__()
        self._options = client_options
        self._client: httpx.AsyncClient | None = None
        self.on_startup(self._open)
        self.on_shutdown(self._close)

    async def _open(self) -> None:
        self._client = httpx.AsyncClient(**self._options)

    async def _close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(self, request: HTTPRequest) -> HTTPResponse:
        if self._client is None:
            raise RuntimeError("HTTP driver is not running")
        response = await self._client.request(
            request.method,
            request.url,
            headers=request.headers,
            content=request.content,
            timeout=request.timeout,
        )
        return HTTPResponse(response.status_code, dict(response.headers), response.content)
