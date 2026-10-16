"""S3 host delegation tests: unforgeable context, scope and deadline propagation."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

import httpx
import pytest

from opsmesh_sdk.context import InvocationContext, InvocationSubject
from opsmesh_sdk.errors import SecurityError, TimeoutError
from opsmesh_sdk.transport.contracts import (
    MAX_RESPONSE_BYTES,
    CancellationScope,
    TransportContext,
    TransportRequest,
)
from opsmesh_sdk.transport.host import (
    DELEGATION_HEADER,
    INSTALLATION_HEADER,
    INVOCATION_HEADER,
    NAMESPACE_HEADER,
    PARENT_INVOCATION_HEADER,
    DelegationIdentity,
    HostTransport,
)


def identity(*, parent_invocation_id: Any = None) -> DelegationIdentity:
    return DelegationIdentity(
        installation_id=uuid4(),
        instance_id=uuid4(),
        invocation_id=uuid4(),
        namespace="fixture.package",
        parent_invocation_id=parent_invocation_id,
        deadline_seconds=30.0,
    )


def mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_delegation_headers_are_attached_by_the_host() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["delegation"] = request.headers.get(DELEGATION_HEADER)
        captured["installation"] = request.headers.get(INSTALLATION_HEADER)
        captured["invocation"] = request.headers.get(INVOCATION_HEADER)
        captured["namespace"] = request.headers.get(NAMESPACE_HEADER)
        return httpx.Response(200, json={"ok": True})

    delegation = identity()
    transport = HostTransport("https://host.invalid", delegation, client=mock_client(handler))

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/host/state/k"), TransportContext())

    response = asyncio.run(run())
    assert response.status == 200
    assert captured["delegation"] == "invocation"
    assert captured["installation"] == str(delegation.installation_id)
    assert captured["invocation"] == str(delegation.invocation_id)
    assert captured["namespace"] == "fixture.package"


def test_caller_cannot_forge_the_delegation_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))
    context = TransportContext(identity_headers={INVOCATION_HEADER: str(uuid4())})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/host/state/k"), context)

    with pytest.raises(SecurityError) as excinfo:
        asyncio.run(run())
    assert INVOCATION_HEADER.lower() in excinfo.value.details["forged_headers"]  # type: ignore[operator]


def test_cancelled_delegation_scope_blocks_the_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))
    scope = CancellationScope()
    scope.cancel()
    context = TransportContext(scope=scope)

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/host/state/k"), context)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


def test_delegation_error_envelope_maps_to_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": "permission_denied", "message": "not granted"}},
        )

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))

    async def run() -> Any:
        return await transport.send(
            TransportRequest(path="/host/models/complete"), TransportContext()
        )

    from opsmesh_sdk.errors import AuthorizationError

    with pytest.raises(AuthorizationError):
        asyncio.run(run())


def test_invocation_context_exposes_verified_subject_and_scope() -> None:
    context = InvocationContext(
        InvocationSubject(
            installation_id="inst-1",
            instance_id="instance-1",
            invocation_id="invocation-1",
            namespace="fixture.package",
        )
    )
    assert context.subject.namespace == "fixture.package"
    assert context.cancelled is False
    context.cancel()
    assert context.cancelled is True


def test_invocation_context_rejects_empty_namespace() -> None:
    with pytest.raises(ValueError):
        InvocationContext(
            InvocationSubject(
                installation_id="i",
                instance_id="n",
                invocation_id="v",
                namespace="",
            )
        )


def test_invocation_context_rejects_unbound_host_group() -> None:
    context = InvocationContext(
        InvocationSubject(
            installation_id="i",
            instance_id="n",
            invocation_id="v",
            namespace="fixture.package",
        )
    )
    with pytest.raises(RuntimeError):
        _ = context.state
    with pytest.raises(KeyError):
        context.host("unknown")


def test_delegation_rejects_invalid_deadline() -> None:
    with pytest.raises(ValueError):
        DelegationIdentity(
            installation_id=uuid4(),
            instance_id=uuid4(),
            invocation_id=uuid4(),
            namespace="fixture.package",
            deadline_seconds=0,
        )


def test_delegation_stream_carries_the_delegation_identity() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["delegation"] = request.headers.get(DELEGATION_HEADER)
        captured["session"] = request.headers.get("X-Session-ID")
        captured["custom"] = request.headers.get("X-Custom")
        return httpx.Response(200, text='data: {"ok": true}\n\n')

    delegation = identity()
    transport = HostTransport("https://host.invalid", delegation, client=mock_client(handler))
    request = TransportRequest(path="/host/events", stream=True, headers={"X-Custom": "yes"})

    async def run() -> Any:
        return [chunk async for chunk in transport.stream(request, TransportContext())]

    chunks = asyncio.run(run())
    assert chunks[0].data == {"ok": True}
    assert captured["delegation"] == "invocation"
    assert captured["session"] is None, "a delegated stream never carries a user session"
    assert captured["custom"] == "yes"


def test_delegation_stream_rejects_a_forged_invocation_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='data: {"ok": true}\n\n')

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))
    context = TransportContext(identity_headers={INVOCATION_HEADER: str(uuid4())})
    request = TransportRequest(path="/host/events", stream=True)

    async def run() -> Any:
        return [chunk async for chunk in transport.stream(request, context)]

    with pytest.raises(SecurityError) as excinfo:
        asyncio.run(run())
    assert INVOCATION_HEADER.lower() in excinfo.value.details["forged_headers"]  # type: ignore[operator]


def test_delegation_send_rejects_forged_request_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))
    request = TransportRequest(path="/host/state/k", headers={INVOCATION_HEADER: str(uuid4())})

    async def run() -> Any:
        return await transport.send(request, TransportContext())

    with pytest.raises(SecurityError):
        asyncio.run(run())


def test_delegation_send_rejects_case_variant_forgery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))

    async def run_request_variant() -> Any:
        request = TransportRequest(
            path="/host/state/k", headers={INVOCATION_HEADER.lower(): str(uuid4())}
        )
        return await transport.send(request, TransportContext())

    async def run_context_variant() -> Any:
        context = TransportContext(identity_headers={INVOCATION_HEADER.lower(): str(uuid4())})
        return await transport.send(TransportRequest(path="/host/state/k"), context)

    with pytest.raises(SecurityError):
        asyncio.run(run_request_variant())
    with pytest.raises(SecurityError):
        asyncio.run(run_context_variant())


def test_delegation_send_rejects_trace_header_forgery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))
    context = TransportContext(trace_headers={INVOCATION_HEADER: str(uuid4())})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/host/state/k"), context)

    with pytest.raises(SecurityError) as excinfo:
        asyncio.run(run())
    assert INVOCATION_HEADER.lower() in excinfo.value.details["forged_headers"]  # type: ignore[operator]


def test_delegation_stream_rejects_trace_header_forgery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='data: {"ok": true}\n\n')

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))
    context = TransportContext(trace_headers={INVOCATION_HEADER: str(uuid4())})

    async def run() -> Any:
        request = TransportRequest(path="/host/events", stream=True)
        return [chunk async for chunk in transport.stream(request, context)]

    with pytest.raises(SecurityError):
        asyncio.run(run())


def test_trace_headers_propagate_without_touching_the_identity() -> None:
    captured: dict[str, Any] = {}
    delegation = identity()

    def handler(request: httpx.Request) -> httpx.Response:
        captured["traceparent"] = request.headers.get("traceparent")
        captured["invocation"] = request.headers.get(INVOCATION_HEADER)
        captured["delegation"] = request.headers.get(DELEGATION_HEADER)
        return httpx.Response(200, json={"ok": True})

    transport = HostTransport("https://host.invalid", delegation, client=mock_client(handler))
    context = TransportContext(trace_headers={"traceparent": "00-" + "0" * 32 + "-01"})

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/host/state/k"), context)

    asyncio.run(run())
    assert captured["traceparent"] == "00-" + "0" * 32 + "-01"
    assert captured["invocation"] == str(delegation.invocation_id)
    assert captured["delegation"] == "invocation"


def test_parent_invocation_id_travels_in_the_delegation_headers() -> None:
    captured: dict[str, Any] = {}
    parent = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        captured["parent"] = request.headers.get(PARENT_INVOCATION_HEADER)
        captured["namespace"] = request.headers.get(NAMESPACE_HEADER)
        return httpx.Response(200, json={"ok": True})

    transport = HostTransport(
        "https://host.invalid", identity(parent_invocation_id=parent), client=mock_client(handler)
    )

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/host/state/k"), TransportContext())

    asyncio.run(run())
    assert captured["parent"] == str(parent)
    assert captured["namespace"] == "fixture.package"

    root_captured: dict[str, Any] = {}

    def root_handler(request: httpx.Request) -> httpx.Response:
        root_captured["parent"] = request.headers.get(PARENT_INVOCATION_HEADER)
        return httpx.Response(200, json={"ok": True})

    root_transport = HostTransport(
        "https://host.invalid", identity(), client=mock_client(root_handler)
    )

    async def run_root() -> Any:
        return await root_transport.send(TransportRequest(path="/host/state/k"), TransportContext())

    asyncio.run(run_root())
    assert root_captured["parent"] is None, "a root invocation carries no parent"


def test_host_response_size_bound_is_enforced() -> None:
    oversized = b"x" * (MAX_RESPONSE_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized)

    transport = HostTransport("https://host.invalid", identity(), client=mock_client(handler))

    async def run() -> Any:
        return await transport.send(TransportRequest(path="/host/state/k"), TransportContext())

    with pytest.raises(Exception) as excinfo:  # noqa: B017 - TransportError from the bound
        asyncio.run(run())
    assert "size bound" in str(excinfo.value)


def test_host_deadline_bounds_the_request_before_its_own_timeout() -> None:
    class DelayingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            timeout = request.extensions.get("timeout")
            read = timeout.get("read") if isinstance(timeout, dict) else None
            wait = 0.4 if read is None else min(0.4, read)
            await asyncio.sleep(wait)
            if read is not None and read < 0.4:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(200, json={"ok": True})

    transport = HostTransport(
        "https://host.invalid",
        identity(),
        client=httpx.AsyncClient(transport=DelayingTransport()),
    )
    scope = CancellationScope(deadline_seconds=0.1)
    context = TransportContext(scope=scope)
    request = TransportRequest(path="/host/state/k", timeout_seconds=30.0)

    async def run() -> Any:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await transport.send(request, context)
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 1.0, "the scope deadline, not the request timeout, must end the call"
