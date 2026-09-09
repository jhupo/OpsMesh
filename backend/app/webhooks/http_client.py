from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

from backend.app.webhooks.constants import WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH


class WebhookHttpClient(Protocol):
    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> WebhookHttpResponse: ...


@dataclass(frozen=True)
class WebhookHttpResponse:
    status_code: int
    body: str
    headers: dict[str, str]


class HttpxWebhookHttpClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> WebhookHttpResponse:
        with httpx.Client(
            follow_redirects=False,
            transport=self._transport,
        ) as client, client.stream(
            "POST",
            url,
            content=body,
            headers=headers,
            timeout=timeout_seconds,
        ) as response:
            return WebhookHttpResponse(
                status_code=response.status_code,
                body=_response_body_snippet(response),
                headers=dict(response.headers.items()),
            )


def _response_body_snippet(response: httpx.Response) -> str:
    content = bytearray()
    for chunk in response.iter_bytes():
        remaining = WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH - len(content)
        if remaining <= 0:
            break
        content.extend(chunk[:remaining])
    return bytes(content).decode("utf-8", errors="replace")
