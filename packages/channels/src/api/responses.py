"""Encode the channel's JSON envelopes and SSE frames."""

import json
from typing import Any

from starlette.responses import Response

from .errors import HTTPError

_JSON_TYPE = "application/json; charset=utf-8"


def encode_json(payload: Any) -> bytes:
    """Serialize a contract value without exposing Python objects."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def json_response(payload: Any, *, status_code: int = 200) -> Response:
    """Return one buffered JSON response."""
    return Response(
        status_code=status_code,
        headers={"content-type": _JSON_TYPE},
        content=encode_json(payload),
    )


def error_response(error: HTTPError) -> Response:
    """Map a safe request error to a JSON response."""
    return Response(
        status_code=error.status_code,
        headers={"content-type": _JSON_TYPE, **error.headers},
        content=encode_json({"error": error.message}),
    )


def sse_event(event: str, data: Any) -> bytes:
    """Encode one complete server-sent event frame."""
    if not event or "\n" in event:
        raise ValueError("SSE event names must be a single non-empty line")
    payload = encode_json(data)
    return b"event: " + event.encode("utf-8") + b"\ndata: " + payload + b"\n\n"
