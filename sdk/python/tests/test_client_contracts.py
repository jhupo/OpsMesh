"""S3 transport contract tests: serialization, identity, errors, cancellation, streams."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest

from opsmesh_sdk.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    SecurityError,
    TimeoutError,
    TransportError,
    ValidationError,
)
from opsmesh_sdk.transport.contracts import (
    MAX_RESPONSE_BYTES,
    SESSION_HEADER,
    CancellationScope,
    TransportContext,
    TransportRequest,
)
from opsmesh_sdk.transport.http import IDEMPOTENCY_HEADER, HttpTransport
from opsmesh_sdk.transport.streams import EventStreamReader, parse_sse_event


class DelayingTransport(httpx.AsyncBaseTransport):
    """An async transport that honors the effective read timeout like httpx does."""

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.effective_timeouts: list[Any] = []
        self.captured_headers: list[httpx.Headers] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout")
        self.effective_timeouts.append(timeout)
        self.captured_headers.append(request.headers)
        read = timeout.get("read") if isinstance(timeout, dict) else None
        wait = self.delay_seconds if read is None else min(self.delay_seconds, read)
        await asyncio.sleep(wait)
        if read is not None and read < self.delay_seconds:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"ok": True})


def mock_transport(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_handler(payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers=headers or {})

    return handler


def test_request_serializes_body_and_identity() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["session"] = request.headers.get(SESSION_HEADER)
        captured["idempotency"] = request.headers.get(IDEMPOTENCY_HEADER)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=mock_transport(handler)
    )
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})
    request = TransportRequest(
        method="POST",
        path="/api/v1/things",
        body={"name": "thing"},
        idempotency_key="idem-key-12345678",
    )

    async def run() -> Any:
        return await transport.send(request, context)

    response = asyncio.run(run())
    assert response.status == 200
    assert response.body == {"ok": True}
    assert captured["session"] == "session-1"
    assert captured["idempotency"] == "idem-key-12345678"
    assert captured["body"] == {"name": "thing"}


def test_server_error_envelope_maps_to_typed_error() -> None:
    transport = HttpTransport(
        "https://api.invalid",
        session_id="session-1",
        client=mock_transport(
            json_handler(
                {
                    "error": {
                        "code": "conflict",
                        "message": "idempotency key reused",
                        "request_id": "req-1",
                    }
                },
                status=409,
            )
        ),
    )
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/api/v1/things"), context)

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.request_id == "req-1"
    assert excinfo.value.code == "conflict"


def test_not_found_and_rate_limit_map_to_typed_errors() -> None:
    transport = HttpTransport(
        "https://api.invalid",
        session_id="session-1",
        client=mock_transport(
            json_handler({"error": {"code": "not_found", "message": "missing"}}, status=404)
        ),
    )
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/api/v1/things/1"), context)

    with pytest.raises(NotFoundError):
        asyncio.run(run())


def test_rate_limit_carries_retry_after() -> None:
    transport = HttpTransport(
        "https://api.invalid",
        session_id="session-1",
        client=mock_transport(
            json_handler(
                {"error": {"code": "rate_limited", "message": "slow down"}},
                status=429,
                headers={"Retry-After": "7"},
            )
        ),
    )
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/api/v1/things"), context)

    with pytest.raises(RateLimitError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.retry_after == 7.0


def test_missing_session_identity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        HttpTransport("https://api.invalid", session_id="")


def test_cancelled_scope_blocks_the_call() -> None:
    transport = HttpTransport(
        "https://api.invalid",
        session_id="session-1",
        client=mock_transport(json_handler({"ok": True})),
    )
    scope = CancellationScope()
    scope.cancel()
    context = TransportContext(scope=scope, identity_headers={SESSION_HEADER: "session-1"})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/api/v1/things"), context)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


def test_deadline_timeout_maps_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=mock_transport(handler)
    )
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/api/v1/things"), context)

    with pytest.raises(TimeoutError):
        asyncio.run(run())


def test_undecidable_failure_is_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=mock_transport(handler)
    )
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/api/v1/things"), context)

    with pytest.raises(TransportError):
        asyncio.run(run())


def test_event_stream_reads_chunks_and_keeps_the_cursor() -> None:
    body = (
        'id: 1\nevent: run.event\ndata: {"sequence": 1}\n\n'
        'id: 2\nevent: run.event\ndata: {"sequence": 2}\n\n'
    )
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["last_event_id"] = request.headers.get("Last-Event-ID")
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = mock_transport(handler)
    reader = EventStreamReader(client, base_url=httpx.URL("https://api.invalid"))
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})
    request = TransportRequest(method="GET", path="/api/v1/events", stream=True)

    async def run() -> Any:
        return [chunk async for chunk in reader.read(request, context)]

    chunks = asyncio.run(run())
    assert [chunk.cursor for chunk in chunks] == ["1", "2"]
    assert chunks[0].data == {"sequence": 1}
    assert captured["last_event_id"] is None

    async def run_resumed() -> Any:
        resumed = TransportRequest(
            method="GET", path="/api/v1/events", stream=True, last_event_id="2"
        )
        return [chunk async for chunk in reader.read(resumed, context)]

    asyncio.run(run_resumed())
    assert captured["last_event_id"] == "2"


def test_sse_parser_handles_comments_and_multiline_data() -> None:
    event = parse_sse_event([": keep-alive", "data: line-1", "data: line-2", "id: 9"])
    assert event is not None
    assert event.data == "line-1\nline-2"
    assert event.event_id == "9"
    assert parse_sse_event([": only a comment"]) is None


def test_stream_rejection_maps_to_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "unavailable", "message": "later"}})

    reader = EventStreamReader(mock_transport(handler), base_url=httpx.URL("https://api.invalid"))
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})

    async def run() -> Any:
        return [chunk async for chunk in reader.read(TransportRequest(path="/api/v1/e"), context)]

    with pytest.raises(TransportError):
        asyncio.run(run())


def test_authentication_error_is_typed() -> None:
    transport = HttpTransport(
        "https://api.invalid",
        session_id="session-1",
        client=mock_transport(
            json_handler({"error": {"code": "unauthenticated", "message": "expired"}}, status=401)
        ),
    )
    context = TransportContext(identity_headers={SESSION_HEADER: "session-1"})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/api/v1/me"), context)

    with pytest.raises(AuthenticationError):
        asyncio.run(run())


def test_request_headers_reach_the_server_and_identity_is_protected() -> None:
    delaying = DelayingTransport()
    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=httpx.AsyncClient(transport=delaying)
    )
    request = TransportRequest(path="/api/v1/things", headers={"X-Custom": "yes"})

    async def run() -> Any:
        return await transport.send(request, TransportContext())

    asyncio.run(run())
    sent = delaying.captured_headers[0]
    assert sent["X-Custom"] == "yes"
    assert sent[SESSION_HEADER] == "session-1"

    async def run_forged() -> Any:
        forged = TransportRequest(path="/api/v1/things", headers={SESSION_HEADER: "forged"})
        return await transport.send(forged, TransportContext())

    with pytest.raises(SecurityError):
        asyncio.run(run_forged())


def test_deadline_bounds_the_request_before_its_own_timeout() -> None:
    delaying = DelayingTransport(delay_seconds=0.4)
    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=httpx.AsyncClient(transport=delaying)
    )
    scope = CancellationScope(deadline_seconds=0.1)
    context = TransportContext(scope=scope)
    request = TransportRequest(path="/api/v1/things", timeout_seconds=30.0)

    async def run() -> Any:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await transport.send(request, context)
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 1.0, "the scope deadline, not the request timeout, must end the call"
    assert scope.expired is True
    effective = delaying.effective_timeouts[0]
    assert isinstance(effective, dict) and effective["read"] <= 0.1, (
        "the effective read timeout must be the remaining deadline"
    )


def test_request_timeout_applies_without_a_deadline() -> None:
    delaying = DelayingTransport(delay_seconds=0.3)
    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=httpx.AsyncClient(transport=delaying)
    )
    request = TransportRequest(path="/api/v1/things", timeout_seconds=0.05)

    async def run() -> Any:
        return await transport.send(request, TransportContext())

    with pytest.raises(TimeoutError):
        asyncio.run(run())


def test_cancel_interrupts_an_in_flight_request() -> None:
    delaying = DelayingTransport(delay_seconds=1.0)
    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=httpx.AsyncClient(transport=delaying)
    )
    scope = CancellationScope()
    context = TransportContext(scope=scope)
    request = TransportRequest(path="/api/v1/things", timeout_seconds=30.0)

    async def run() -> Any:
        started = time.monotonic()
        call = asyncio.ensure_future(transport.send(request, context))
        await asyncio.sleep(0.05)
        scope.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 0.8, "cancellation must interrupt the request instead of awaiting it"


def test_http_stream_sends_request_headers_and_keeps_the_session() -> None:
    body = 'data: {"sequence": 1}\n\n'
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["custom"] = request.headers.get("X-Custom")
        captured["session"] = request.headers.get(SESSION_HEADER)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=mock_transport(handler)
    )
    request = TransportRequest(
        method="GET", path="/api/v1/events", stream=True, headers={"X-Custom": "yes"}
    )

    async def run() -> Any:
        return [chunk async for chunk in transport.stream(request, TransportContext())]

    chunks = asyncio.run(run())
    assert chunks[0].data == {"sequence": 1}
    assert captured["custom"] == "yes"
    assert captured["session"] == "session-1"


def test_http_stream_rejects_a_forged_session_header() -> None:
    transport = HttpTransport(
        "https://api.invalid",
        session_id="session-1",
        client=mock_transport(json_handler({"ok": True})),
    )
    request = TransportRequest(
        method="GET", path="/api/v1/events", stream=True, headers={SESSION_HEADER: "forged"}
    )

    async def run() -> Any:
        return [chunk async for chunk in transport.stream(request, TransportContext())]

    with pytest.raises(SecurityError):
        asyncio.run(run())


class HangingStreamTransport(httpx.AsyncBaseTransport):
    """A stream transport whose body arrives only after a delay."""

    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout")
        read = timeout.get("read") if isinstance(timeout, dict) else None
        return httpx.Response(
            200,
            stream=_DelayedBytes(self.delay_seconds, read_timeout=read),
            headers={"content-type": "text/event-stream"},
        )


class _DelayedBytes(httpx.AsyncByteStream):
    def __init__(self, delay_seconds: float, *, read_timeout: float | None = None) -> None:
        self.delay_seconds = delay_seconds
        self.read_timeout = read_timeout

    async def __aiter__(self) -> Any:
        wait = (
            self.delay_seconds
            if self.read_timeout is None
            else min(self.delay_seconds, self.read_timeout)
        )
        await asyncio.sleep(wait)
        if self.read_timeout is not None and self.read_timeout < self.delay_seconds:
            raise httpx.ReadTimeout("the stream idled past its bound")
        yield b'data: {"ok": true}\n\n'


def test_event_stream_deadline_bounds_the_idle_wait() -> None:
    transport = HttpTransport(
        "https://api.invalid",
        session_id="session-1",
        client=httpx.AsyncClient(transport=HangingStreamTransport(delay_seconds=1.0)),
    )
    scope = CancellationScope(deadline_seconds=0.1)
    context = TransportContext(scope=scope)
    request = TransportRequest(method="GET", path="/api/v1/events", stream=True)

    async def run() -> Any:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            _ = [chunk async for chunk in transport.stream(request, context)]
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 0.8, "the scope deadline, not the idle bound, must end the stream"


def test_event_stream_cancel_interrupts_a_stalled_stream() -> None:
    transport = HttpTransport(
        "https://api.invalid",
        session_id="session-1",
        client=httpx.AsyncClient(transport=HangingStreamTransport(delay_seconds=1.0)),
    )
    scope = CancellationScope()
    context = TransportContext(scope=scope)
    request = TransportRequest(method="GET", path="/api/v1/events", stream=True)

    async def run() -> Any:
        started = time.monotonic()
        chunks = transport.stream(request, context)

        async def consume() -> Any:
            return [chunk async for chunk in chunks]

        call = asyncio.ensure_future(consume())
        await asyncio.sleep(0.05)
        scope.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 0.8, "cancellation must interrupt a stalled stream"


def test_response_size_bound_is_enforced() -> None:
    oversized = b"x" * (MAX_RESPONSE_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized)

    transport = HttpTransport(
        "https://api.invalid", session_id="session-1", client=mock_transport(handler)
    )

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/api/v1/things"), TransportContext())

    with pytest.raises(TransportError):
        asyncio.run(run())
