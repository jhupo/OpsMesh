"""Transport contracts: requests, responses, streams and cancellation.

The contract is deliberately small: one request in, one decidable response
out, plus a bounded stream of chunks. Errors are typed by the caller-facing
``errors`` module; the transport only classifies decidable server answers.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.errors import AuthenticationError

DEFAULT_TIMEOUT_SECONDS: Final = 30.0
MAX_RESPONSE_BYTES: Final = 8 * 1_048_576
SESSION_HEADER: Final = "X-Session-ID"


class TransportRequest(BaseModel):
    """One transport-level call; the body is already the serialized payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = Field(default="POST", pattern="^(GET|POST|PATCH|PUT|DELETE)$")
    path: str = Field(min_length=1, max_length=2048)
    query: Mapping[str, str] = Field(default_factory=dict)
    headers: Mapping[str, str] = Field(default_factory=dict)
    body: JsonValue = None
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0, le=600)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    stream: bool = False
    last_event_id: str | None = Field(default=None, max_length=512)


class TransportResponse(BaseModel):
    """A decidable server answer: status, headers and parsed JSON body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: int = Field(ge=100, le=599)
    headers: Mapping[str, str] = Field(default_factory=dict)
    body: JsonValue = None
    request_id: str | None = Field(default=None, max_length=128)
    retry_after: float | None = Field(default=None, ge=0)


class StreamChunk(BaseModel):
    """One bounded chunk of a streamed answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data: JsonValue = None
    event_id: str | None = Field(default=None, max_length=512)
    cursor: str | None = Field(default=None, max_length=512)


class CancellationScope:
    """Propagates caller cancellation and deadlines into the transport."""

    def __init__(self, *, deadline_seconds: float | None = None) -> None:
        self._deadline_seconds = deadline_seconds
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def deadline_seconds(self) -> float | None:
        return self._deadline_seconds

    def cancel(self) -> None:
        self._cancelled = True

    def ensure_active(self) -> None:
        if self._cancelled:
            raise asyncio.CancelledError


@dataclass(slots=True)
class TransportContext:
    """Per-call context: identity headers, trace context and cancellation."""

    scope: CancellationScope = dataclass_field(default_factory=CancellationScope)
    identity_headers: Mapping[str, str] = dataclass_field(default_factory=dict)
    trace_headers: Mapping[str, str] = dataclass_field(default_factory=dict)


def require_session(context: TransportContext) -> str:
    """Read the session identity from the context; a missing one is fatal."""
    value = context.identity_headers.get(SESSION_HEADER)
    if value is None:
        raise AuthenticationError("A platform session is required")
    return value


class Transport(Protocol):
    """The single interface every transport implementation satisfies."""

    async def send(
        self, request: TransportRequest, context: TransportContext
    ) -> TransportResponse: ...

    def stream(
        self, request: TransportRequest, context: TransportContext
    ) -> AsyncIterator[StreamChunk]: ...

    async def aclose(self) -> None: ...


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_RESPONSE_BYTES",
    "SESSION_HEADER",
    "CancellationScope",
    "StreamChunk",
    "Transport",
    "TransportContext",
    "TransportRequest",
    "TransportResponse",
    "require_session",
]
