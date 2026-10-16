"""S3 transport contract tests: serialization, identity, errors, cancellation, streams."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from opsmesh_sdk.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    TimeoutError,
    TransportError,
    ValidationError,
)
from opsmesh_sdk.transport.contracts import (
    SESSION_HEADER,
    CancellationScope,
    TransportContext,
    TransportRequest,
)
from opsmesh_sdk.transport.http import IDEMPOTENCY_HEADER, HttpTransport
from opsmesh_sdk.transport.streams import EventStreamReader, parse_sse_event


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
