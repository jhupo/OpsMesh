from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from uuid import UUID

from opsmesh.shared.security.egress import EgressURLPolicy, validate_egress_url

_URL_POLICY = EgressURLPolicy(
    allowed_schemes=frozenset({"https"}),
    allow_unresolved_hosts=False,
)


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class KnowledgeURLFetchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HTTPKnowledgeURLFetcher:
    """Fetch a knowledge source from a Temporal Activity with explicit byte limits."""

    def __init__(self, *, timeout_seconds: int) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds

    def fetch(self, *, workspace_id: UUID, url: str, max_bytes: int) -> bytes:
        del workspace_id
        _validate_url(url)
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "OpsMesh-KnowledgeFetcher/1"},
            method="GET",
        )
        try:
            opener = urllib.request.build_opener(_ValidatedRedirectHandler())
            with opener.open(request, timeout=self._timeout_seconds) as response:
                payload = bytes(response.read(max_bytes + 1))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise KnowledgeURLFetchError(
                "url_fetch_failed", "Knowledge source URL fetch failed"
            ) from exc
        if len(payload) > max_bytes:
            raise KnowledgeURLFetchError(
                "source_too_large", "Knowledge source exceeds the byte limit"
            )
        return payload


def _validate_url(value: str) -> None:
    validate_egress_url(value, policy=_URL_POLICY)
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise KnowledgeURLFetchError("fetch_url_invalid", "Knowledge source URL must use HTTPS")
