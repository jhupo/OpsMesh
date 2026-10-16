"""Bounded SSE/event reading with cursor continuation.

Content is released only after the server marks it complete; the reader keeps
the last event id so an interrupted stream resumes from its cursor instead of
re-reading from the beginning.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Final

import httpx

from opsmesh_sdk.errors import TransportError
from opsmesh_sdk.transport.contracts import (
    StreamChunk,
    TransportContext,
    TransportRequest,
)

MAX_IDLE_SECONDS: Final = 300.0
MAX_CHUNK_BYTES: Final = 1_048_576


class SseEvent:
    """One parsed server-sent event."""

    def __init__(self, *, event_id: str | None, data: str, event: str | None = None) -> None:
        self.event_id = event_id
        self.data = data
        self.event = event

    @property
    def cursor(self) -> str | None:
        return self.event_id


def parse_sse_event(lines: list[str]) -> SseEvent | None:
    """Parse accumulated field lines into one event; blank lines separate events."""
    event_id: str | None = None
    event: str | None = None
    data_lines: list[str] = []
    for line in lines:
        if not line or line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "id":
            event_id = value
        elif field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
    if event_id is None and event is None and not data_lines:
        return None
    return SseEvent(event_id=event_id, data="\n".join(data_lines), event=event)


class EventStreamReader:
    """Reads bounded event streams with cursor continuation and idle bounds."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: httpx.URL,
        max_idle_seconds: float = MAX_IDLE_SECONDS,
        identity_headers: Mapping[str, str] | None = None,
    ) -> None:
        if max_idle_seconds <= 0:
            raise ValueError("max_idle_seconds must be positive")
        self._client = client
        self._base_url = base_url
        self._max_idle_seconds = max_idle_seconds
        # The owning transport injects its identity (session or delegation);
        # a per-call context may still override individual headers.
        self._identity_headers: dict[str, str] = dict(identity_headers or {})

    async def read(
        self, request: TransportRequest, context: TransportContext
    ) -> AsyncIterator[StreamChunk]:
        headers = {**self._identity_headers, **context.identity_headers}
        headers.update(context.trace_headers)
        if request.last_event_id is not None:
            headers["Last-Event-ID"] = request.last_event_id
        try:
            async with self._client.stream(
                request.method,
                self._base_url.join(request.path),
                params=dict(request.query),
                json=request.body,
                headers=headers,
                timeout=httpx.Timeout(self._max_idle_seconds, read=self._max_idle_seconds),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise TransportError(
                        "The event stream was rejected",
                        details={"status": response.status_code},
                    )
                pending: list[str] = []
                async for line in response.aiter_lines():
                    context.scope.ensure_active()
                    if line == "":
                        event = parse_sse_event(pending)
                        pending.clear()
                        if event is None:
                            continue
                        yield _to_chunk(event)
                        continue
                    if len(line) > MAX_CHUNK_BYTES:
                        raise TransportError("An event line exceeds the chunk size bound")
                    pending.append(line)
        except httpx.TimeoutException as exc:
            raise TransportError("The event stream idled past its bound") from exc
        except httpx.HTTPError as exc:
            raise TransportError(
                "The event stream failed", details={"error_code": type(exc).__name__}
            ) from exc


def _to_chunk(event: SseEvent) -> StreamChunk:
    data: object = None
    if event.data:
        try:
            data = json.loads(event.data)
        except json.JSONDecodeError:
            data = event.data
    return StreamChunk(data=data, event_id=event.event_id, cursor=event.cursor)


__all__ = [
    "MAX_CHUNK_BYTES",
    "MAX_IDLE_SECONDS",
    "EventStreamReader",
    "SseEvent",
    "parse_sse_event",
]
