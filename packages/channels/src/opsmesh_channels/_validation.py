"""Shared validation for channel configuration and callback paths."""

from __future__ import annotations

import re

from yarl import URL

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def http_url(value: str) -> str:
    """Validate an absolute HTTP API base without credentials or a query."""
    url = URL(value)
    if url.scheme not in {"http", "https"} or not url.host:
        raise ValueError("must be an absolute http(s) URL")
    if url.query_string or url.fragment or url.user or url.password:
        raise ValueError("must not contain credentials, a query, or a fragment")
    return value.rstrip("/")


def path_prefix(value: str) -> str:
    """Normalize a callback path prefix below the server root."""
    if not value.startswith("/"):
        raise ValueError("must start with '/'")
    if "?" in value or "#" in value or "\\" in value:
        raise ValueError("contains an invalid character")
    segments = [segment for segment in value.split("/") if segment]
    if not segments:
        raise ValueError("must contain at least one segment")
    for segment in segments:
        if segment in {".", ".."}:
            raise ValueError("must not traverse path segments")
        if "{" in segment or "}" in segment:
            raise ValueError("must not contain path parameters")
        if any(ord(char) < 0x20 for char in segment):
            raise ValueError("contains a control character")
    return "/" + "/".join(segments)


def connection_segment(value: str, label: str) -> str:
    """Validate one namespace component used in a callback path."""
    if not _SEGMENT.fullmatch(value):
        raise ValueError(f"{label} must be a URL-safe identifier")
    if ".." in value:
        raise ValueError(f"{label} must not contain '..'")
    return value
