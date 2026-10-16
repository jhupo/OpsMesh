"""Host delegation transport: the restricted channel of one Invocation.

Extensions never receive a user session or admin credentials. The host
attaches the verified invocation identity (installation, instance,
invocation, parent chain, namespace and deadline) to every delegated call,
and the transport refuses to send a context whose delegation header was
supplied by the caller instead of the host.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Final
from uuid import UUID

import httpx

from opsmesh_sdk.errors import (
    TimeoutError,
    TransportError,
    error_for_code,
)
from opsmesh_sdk.transport.contracts import (
    MAX_RESPONSE_BYTES,
    StreamChunk,
    TransportContext,
    TransportRequest,
    TransportResponse,
    build_call_headers,
    resolve_timeout,
    send_cancellable,
)
from opsmesh_sdk.transport.streams import EventStreamReader

DELEGATION_HEADER: Final = "X-OpsMesh-Delegation"
INSTALLATION_HEADER: Final = "X-OpsMesh-Installation-Id"
INSTANCE_HEADER: Final = "X-OpsMesh-Instance-Id"
INVOCATION_HEADER: Final = "X-OpsMesh-Invocation-Id"
PARENT_INVOCATION_HEADER: Final = "X-OpsMesh-Parent-Invocation-Id"
NAMESPACE_HEADER: Final = "X-OpsMesh-Namespace"
DEADLINE_HEADER: Final = "X-OpsMesh-Deadline"
_FORBIDDEN_CALLER_HEADERS: Final = frozenset(
    {
        DELEGATION_HEADER,
        INSTALLATION_HEADER,
        INSTANCE_HEADER,
        INVOCATION_HEADER,
        PARENT_INVOCATION_HEADER,
        NAMESPACE_HEADER,
        DEADLINE_HEADER,
    }
)


class DelegationIdentity:
    """The verified identity of the current invocation, set by the host only."""

    def __init__(
        self,
        *,
        installation_id: UUID,
        instance_id: UUID,
        invocation_id: UUID,
        namespace: str,
        parent_invocation_id: UUID | None = None,
        deadline_seconds: float | None = None,
    ) -> None:
        if not namespace:
            raise ValueError("a delegated invocation requires its package namespace")
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise ValueError("the delegation deadline must be positive")
        self.installation_id = installation_id
        self.instance_id = instance_id
        self.invocation_id = invocation_id
        self.parent_invocation_id = parent_invocation_id
        self.namespace = namespace
        self.deadline_seconds = deadline_seconds

    def headers(self) -> dict[str, str]:
        headers = {
            DELEGATION_HEADER: "invocation",
            INSTALLATION_HEADER: str(self.installation_id),
            INSTANCE_HEADER: str(self.instance_id),
            INVOCATION_HEADER: str(self.invocation_id),
            NAMESPACE_HEADER: self.namespace,
        }
        if self.parent_invocation_id is not None:
            headers[PARENT_INVOCATION_HEADER] = str(self.parent_invocation_id)
        if self.deadline_seconds is not None:
            headers[DEADLINE_HEADER] = str(self.deadline_seconds)
        return headers


class HostTransport:
    """The restricted delegation channel an extension uses for platform calls."""

    def __init__(
        self,
        base_url: str,
        identity: DelegationIdentity,
        *,
        timeout_seconds: float = 30.0,
        max_connections: int = 10,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if max_connections < 1:
            raise ValueError("max_connections must be positive")
        self._identity = identity
        self._base_url = httpx.URL(base_url.rstrip("/"))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=max_connections),
        )
        self._stream_reader = EventStreamReader(
            self._client,
            base_url=self._base_url,
            identity_headers=identity.headers(),
            protected_headers=_FORBIDDEN_CALLER_HEADERS,
        )

    async def send(self, request: TransportRequest, context: TransportContext) -> TransportResponse:
        context.scope.ensure_active()
        headers = build_call_headers(
            request,
            context,
            identity=self._identity.headers(),
            protected=_FORBIDDEN_CALLER_HEADERS,
        )
        if request.idempotency_key is not None:
            headers["Idempotency-Key"] = request.idempotency_key
        timeout = resolve_timeout(request, context.scope)
        try:
            response = await send_cancellable(
                context.scope,
                lambda: self._client.request(
                    request.method,
                    self._base_url.join(request.path),
                    params=dict(request.query),
                    json=request.body,
                    headers=headers,
                    timeout=timeout,
                ),
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError("The host did not answer within the deadline") from exc
        except httpx.HTTPError as exc:
            raise TransportError(
                "The host could not be reached", details={"error_code": type(exc).__name__}
            ) from exc
        return self._to_response(response)

    def stream(
        self, request: TransportRequest, context: TransportContext
    ) -> AsyncIterator[StreamChunk]:
        return self._stream_reader.read(request, context)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _to_response(self, response: httpx.Response) -> TransportResponse:
        raw = response.content
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransportError("The host response exceeds the size bound")
        body: object = None
        if raw:
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise TransportError(
                    "The host answer is not valid JSON",
                    details={"status": response.status_code},
                ) from exc
        request_id = response.headers.get("X-Request-ID")
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        if response.is_success:
            return TransportResponse(
                status=response.status_code,
                headers=dict(response.headers),
                body=body,
                request_id=request_id,
                retry_after=retry_after,
            )
        raise _error_for_response(response.status_code, body, request_id, retry_after)


def _error_for_response(
    status: int,
    body: object,
    request_id: str | None,
    retry_after: float | None,
) -> Exception:
    code = "opsmesh_error"
    message = f"The host rejected the delegated call with status {status}"
    details: dict[str, object] = {"status": status}
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", code))
            message = str(error.get("message", message))
            raw_details = error.get("details")
            if isinstance(raw_details, dict):
                details = {**details, **raw_details}
    error_type = error_for_code(code)
    if 500 <= status <= 599:
        error_type = TransportError
        message = "The host failed to answer decidable"
    return error_type(message, request_id=request_id, retry_after=retry_after, details=details)


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


__all__ = [
    "DEADLINE_HEADER",
    "DELEGATION_HEADER",
    "INSTALLATION_HEADER",
    "INSTANCE_HEADER",
    "INVOCATION_HEADER",
    "NAMESPACE_HEADER",
    "PARENT_INVOCATION_HEADER",
    "DelegationIdentity",
    "HostTransport",
]
