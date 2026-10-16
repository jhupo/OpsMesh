"""HTTP transport: X-Session-ID identity, timeouts, pooling and error mapping.

The transport never retries non-idempotent calls and never interprets business
rules: it maps a decidable server answer onto a typed SDK error and leaves
undecidable failures as ``TransportError``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Final

import httpx

from opsmesh_sdk.errors import (
    RateLimitError,
    TimeoutError,
    TransportError,
    ValidationError,
    error_for_code,
)
from opsmesh_sdk.transport.contracts import (
    MAX_RESPONSE_BYTES,
    SESSION_HEADER,
    StreamChunk,
    TransportContext,
    TransportRequest,
    TransportResponse,
    build_call_headers,
    resolve_timeout,
    send_cancellable,
)
from opsmesh_sdk.transport.streams import EventStreamReader

REQUEST_ID_HEADER: Final = "X-Request-ID"
RETRY_AFTER_HEADER: Final = "Retry-After"
IDEMPOTENCY_HEADER: Final = "Idempotency-Key"


class HttpTransport:
    """Async HTTP transport for the platform API."""

    def __init__(
        self,
        base_url: str,
        *,
        session_id: str,
        timeout_seconds: float = 30.0,
        max_connections: int = 20,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if max_connections < 1:
            raise ValueError("max_connections must be positive")
        if not session_id:
            raise ValidationError("a platform session id is required")
        self._session_id = session_id
        # The transport owns URL joining so an injected client without its own
        # base_url behaves exactly like the client it creates itself.
        self._base_url = httpx.URL(base_url.rstrip("/"))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=max_connections),
        )
        self._stream_reader = EventStreamReader(
            self._client,
            base_url=self._base_url,
            identity_headers={SESSION_HEADER: session_id},
            protected_headers=frozenset({SESSION_HEADER}),
            overridable_headers=frozenset({SESSION_HEADER}),
        )

    async def send(self, request: TransportRequest, context: TransportContext) -> TransportResponse:
        context.scope.ensure_active()
        headers = build_call_headers(
            request,
            context,
            identity={SESSION_HEADER: self._session_id},
            protected=frozenset({SESSION_HEADER}),
            overridable=frozenset({SESSION_HEADER}),
        )
        if request.idempotency_key is not None:
            headers[IDEMPOTENCY_HEADER.lower()] = request.idempotency_key
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
            raise TimeoutError("The platform did not answer within the deadline") from exc
        except httpx.HTTPError as exc:
            raise TransportError(
                "The platform could not be reached", details={"error_code": type(exc).__name__}
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
            raise TransportError("The platform response exceeds the size bound")
        body: object = None
        if raw:
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise TransportError(
                    "The platform answer is not valid JSON",
                    details={"status": response.status_code},
                ) from exc
        request_id = response.headers.get(REQUEST_ID_HEADER)
        retry_after = _parse_retry_after(response.headers.get(RETRY_AFTER_HEADER))
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
    message = f"The platform rejected the call with status {status}"
    details: dict[str, object] = {"status": status}
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", code))
            message = str(error.get("message", message))
            envelope_request_id = error.get("request_id")
            if request_id is None and isinstance(envelope_request_id, str):
                request_id = envelope_request_id
            raw_details = error.get("details")
            if isinstance(raw_details, dict):
                details = {**details, **raw_details}
    error_type = error_for_code(code)
    if 500 <= status <= 599 and error_type is not RateLimitError:
        error_type = TransportError
        message = "The platform failed to answer decidable"
    return error_type(
        message,
        request_id=request_id,
        retry_after=retry_after,
        details=details,
    )


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_error_payload(payload: Mapping[str, object]) -> Exception:
    """Map an error envelope onto the typed error; used by host transports too."""
    error = payload.get("error")
    if not isinstance(error, dict):
        raise ValidationError("The error envelope is malformed")
    code = str(error.get("code", "opsmesh_error"))
    message = str(error.get("message", "The platform rejected the call"))
    request_id_value = error.get("request_id")
    retry_after_value = error.get("retry_after")
    retry_after: float | None = None
    if isinstance(retry_after_value, int | float):
        retry_after = float(retry_after_value)
    return error_for_code(code)(
        message,
        request_id=str(request_id_value) if isinstance(request_id_value, str) else None,
        retry_after=retry_after,
    )


__all__ = [
    "IDEMPOTENCY_HEADER",
    "RETRY_AFTER_HEADER",
    "REQUEST_ID_HEADER",
    "SESSION_HEADER",
    "HttpTransport",
    "parse_error_payload",
]
