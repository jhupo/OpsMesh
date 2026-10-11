"""Bounded, data-free diagnostics for untrusted Runtime processes."""

from __future__ import annotations

import hashlib
import re
import traceback
from typing import Any

import httpx

DIAGNOSTIC_META_KEY = "com.opsmesh/diagnostic"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,119}$")
_CATEGORIES = {
    "network",
    "authentication",
    "rate_limit",
    "http",
    "protocol",
    "execution",
    "cancelled",
}
_PHASES = {"login", "business_request", "connect", "tool", "control", "discovery"}


def diagnostic_fields(value: object) -> dict[str, Any]:
    """Never forward arbitrary messages, URLs, bodies or vendor-supplied identities."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, choices in (("category", _CATEGORIES), ("phase", _PHASES)):
        if isinstance(value.get(key), str) and value[key] in choices:
            result[key] = value[key]
    name = value.get("exception_type")
    if isinstance(name, str) and _IDENTIFIER.fullmatch(name):
        result["exception_type"] = name
    status = value.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    duration = value.get("duration_ms")
    if type(duration) is int and 0 <= duration <= 86_400_000:
        result["duration_ms"] = duration
    if type(value.get("retryable")) is bool:
        result["retryable"] = value["retryable"]
    return result


def exception_diagnostic(error: BaseException, *, phase: str) -> dict[str, Any]:
    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    http_status = None
    category = "execution"
    while pending and len(chain) < 8:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            http_status = current.response.status_code
            category = (
                "authentication"
                if http_status in (401, 403)
                else "rate_limit"
                if http_status == 429
                else "http"
            )
        elif isinstance(current, (httpx.RequestError, TimeoutError, ConnectionError)):
            category = "network"
        frames = traceback.extract_tb(current.__traceback__)[-16:]
        # Source lines, local variables, exception messages and paths may contain secrets.
        chain.append(
            {
                "exception_type": type(current).__name__,
                "frames": [
                    {
                        "location_sha256": hashlib.sha256(
                            f"{frame.filename}:{frame.name}".encode()
                        ).hexdigest(),
                        "line": frame.lineno,
                    }
                    for frame in frames
                ],
            }
        )
        cause = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions[:8]))
        if cause is not None:
            pending.append(cause)
    leaf = chain[-1]["exception_type"] if chain else type(error).__name__
    return {
        "category": category,
        "phase": phase,
        "exception_type": leaf,
        "http_status": http_status,
        "retryable": False,
        "diagnostic": {"exceptions": chain},
    }


class RuntimeDiagnosticError(Exception):
    def __init__(self, diagnostic: dict[str, Any]) -> None:
        super().__init__("Runtime MCP operation failed")
        self.diagnostic = diagnostic


def attach_diagnostic(response: dict[str, Any], diagnostic: dict[str, Any]) -> dict[str, Any]:
    if response.get("isError") is not True:
        return response
    metadata = dict(response.get("_meta") or {})
    metadata[DIAGNOSTIC_META_KEY] = {
        **diagnostic_fields(metadata.get(DIAGNOSTIC_META_KEY)),
        **diagnostic,
    }
    return {**response, "_meta": metadata}
