"""S3 host delegation tests: unforgeable context, scope and deadline propagation."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import httpx
import pytest

from opsmesh_sdk.context import InvocationContext, InvocationSubject
from opsmesh_sdk.errors import SecurityError
from opsmesh_sdk.transport.contracts import (
    CancellationScope,
    TransportContext,
    TransportRequest,
)
from opsmesh_sdk.transport.host import (
    DELEGATION_HEADER,
    INSTALLATION_HEADER,
    INVOCATION_HEADER,
    NAMESPACE_HEADER,
    DelegationIdentity,
    HostTransport,
)


def identity() -> DelegationIdentity:
    return DelegationIdentity(
        installation_id=uuid4(),
        instance_id=uuid4(),
        invocation_id=uuid4(),
        namespace="fixture.package",
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
    assert INVOCATION_HEADER in excinfo.value.details["forged_headers"]  # type: ignore[operator]


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
        return httpx.Response(200, text='data: {"ok": true}\n\n')

    delegation = identity()
    transport = HostTransport("https://host.invalid", delegation, client=mock_client(handler))

    async def run() -> Any:
        return [
            chunk
            async for chunk in transport.stream(
                TransportRequest(path="/host/events", stream=True), TransportContext()
            )
        ]

    chunks = asyncio.run(run())
    assert chunks[0].data == {"ok": True}
    assert captured["delegation"] == "invocation"
    assert captured["session"] is None, "a delegated stream never carries a user session"
