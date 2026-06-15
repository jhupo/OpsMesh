from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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


class UrllibWebhookHttpClient:
    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> WebhookHttpResponse:
        request = Request(
            url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                return WebhookHttpResponse(
                    status_code=int(response.status),
                    body=response.read(WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH).decode(
                        "utf-8",
                        errors="replace",
                    ),
                    headers={key: value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            body_text = exc.read(WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH).decode(
                "utf-8",
                errors="replace",
            )
            return WebhookHttpResponse(
                status_code=int(exc.code),
                body=body_text,
                headers={key: value for key, value in exc.headers.items()},
            )
        except URLError as exc:
            raise ConnectionError(str(exc.reason)) from exc
