"""Generic HTTP and server-sent-event response encoding."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import Any

import anyio
from starlette.responses import Response, StreamingResponse

from .errors import HTTPError
from .routes import RouteRegistration
from .streaming import ManagedStreamingResponse

logger = logging.getLogger(__name__)

_JSON_TYPE = "application/json; charset=utf-8"
_SSE_TYPE = "text/event-stream; charset=utf-8"
_DEFAULT_STREAM_ERROR = "stream failed"
_stream_error_message: ContextVar[str] = ContextVar(
    "opsmesh_api_stream_error_message", default=_DEFAULT_STREAM_ERROR
)


def stream_error_scope(message: str):
    """Bind the safe stream error message for the current request."""
    return _stream_error_message.set(message)


def reset_stream_error_scope(token) -> None:
    """Restore the previous stream error message."""
    _stream_error_message.reset(token)


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


def encode_response(
    registration: RouteRegistration, result: Any
) -> tuple[Response, bool]:
    """Encode the declared return contract or transfer a native response."""
    if isinstance(result, Response):
        if not registration.allow_response:
            raise TypeError("The route does not declare a native Response")
        return result, isinstance(result, StreamingResponse)
    adapter = registration.response_adapter
    if adapter is None:
        raise TypeError("The route must return a native Response")
    value = adapter.validate_python(result)
    if value is None:
        return Response(status_code=204), False
    return json_response(adapter.dump_python(value, mode="json")), False


def sse_event(event: str, data: Any) -> bytes:
    """Encode one complete server-sent event frame."""
    if not event or "\n" in event:
        raise ValueError("SSE event names must be a single non-empty line")
    payload = encode_json(data)
    return b"event: " + event.encode("utf-8") + b"\ndata: " + payload + b"\n\n"


def sse_response(
    events: AsyncIterator[bytes],
    *,
    close: Callable[[], Awaitable[None]] | None = None,
    error_message: str | None = None,
) -> Response:
    """Wrap an async byte iterator as an SSE response.

    The plugin owns event formatting. After the response starts, failures
    are reported only as a sanitized error event so no original exception
    text reaches the client. When no message is supplied, the API channel
    setting for the current request is used.
    """
    if error_message is None:
        error_message = _stream_error_message.get()

    async def frames() -> AsyncIterator[bytes]:
        try:
            async for frame in events:
                if isinstance(frame, str):
                    frame = frame.encode("utf-8")
                if not isinstance(frame, bytes):
                    raise TypeError("SSE frames must be bytes or str")
                yield frame
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as error:
            stream.failure = error
            logger.warning("SSE response stream failed", exc_info=True)
            yield sse_event("error", {"error": error_message})

    async def close_stream(error: BaseException | None) -> None:
        try:
            close_source = getattr(events, "aclose", None)
            if close_source is not None:
                await close_source()
        finally:
            if close is not None:
                await close()

    stream = ManagedStreamingResponse(
        frames(),
        on_close=close_stream,
        status_code=200,
        headers={
            "content-type": _SSE_TYPE,
            "cache-control": "no-cache",
            "x-accel-buffering": "no",
        },
    )
    return stream
