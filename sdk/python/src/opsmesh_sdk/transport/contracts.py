"""Transport contracts: requests, responses, streams and cancellation.

The contract is deliberately small: one request in, one decidable response
out, plus a bounded stream of chunks. Errors are typed by the caller-facing
``errors`` module; the transport only classifies decidable server answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Final, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from opsmesh_sdk.errors import (
    AuthenticationError,
    SecurityError,
    TimeoutError,
    TransportError,
    ValidationError,
)

DEFAULT_TIMEOUT_SECONDS: Final = 30.0
MAX_RESPONSE_BYTES: Final = 8 * 1_048_576
SESSION_HEADER: Final = "X-Session-ID"
CONTENT_TYPE_HEADER: Final = "Content-Type"
JSON_MEDIA_TYPES: Final = frozenset({"application/json", "text/json"})

_T = TypeVar("_T")


class TransportRequest(BaseModel):
    """One transport-level call; the body is already the serialized payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = Field(default="POST", pattern="^(GET|POST|PATCH|PUT|DELETE)$")
    path: str = Field(min_length=1, max_length=2048)
    query: Mapping[str, str] = Field(default_factory=dict)
    headers: Mapping[str, str] = Field(default_factory=dict)
    body: JsonValue = None
    content: bytes | None = None
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0, le=600)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    stream: bool = False
    last_event_id: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def reject_dual_payloads(self) -> TransportRequest:
        """A call carries a JSON body or raw content, never both at once."""
        if self.content is not None and self.body is not None:
            raise ValueError("a request carries either a JSON body or raw content, not both")
        return self


class TransportResponse(BaseModel):
    """A decidable server answer: status, headers and parsed JSON body.

    ``body`` holds the decoded JSON value and ``content`` the raw bytes of a
    non-JSON answer (an image, a file download); exactly one is set.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: int = Field(ge=100, le=599)
    headers: Mapping[str, str] = Field(default_factory=dict)
    body: JsonValue = None
    content: bytes | None = None
    request_id: str | None = Field(default=None, max_length=128)
    retry_after: float | None = Field(default=None, ge=0)


class StreamChunk(BaseModel):
    """One bounded chunk of a streamed answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data: JsonValue = None
    event_id: str | None = Field(default=None, max_length=512)
    cursor: str | None = Field(default=None, max_length=512)


class CancellationScope:
    """Propagates caller cancellation and deadlines into the transport.

    The deadline starts when the scope is created and elapses on the monotonic
    clock, so it bounds the whole call including connection setup. Cancellation
    is cooperative: waiters register a future in their own event loop, so one
    scope may be observed from successive call cycles without binding itself
    to a single loop.
    """

    def __init__(self, *, deadline_seconds: float | None = None) -> None:
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        self._deadline_seconds = deadline_seconds
        self._cancelled = False
        self._started_monotonic = time.monotonic()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def deadline_seconds(self) -> float | None:
        return self._deadline_seconds

    def remaining_seconds(self) -> float | None:
        """Time left before the deadline elapses; 0.0 once it has elapsed."""
        if self._deadline_seconds is None:
            return None
        elapsed = time.monotonic() - self._started_monotonic
        return max(self._deadline_seconds - elapsed, 0.0)

    @property
    def expired(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0.0

    def cancel(self) -> None:
        """Signal cancellation; safe to call from any thread, more than once."""
        if self._cancelled:
            return
        self._cancelled = True
        waiters, self._waiters = self._waiters, []
        for loop, future in waiters:
            _resolve_threadsafe(loop, future)

    def register_cancellation(self) -> asyncio.Future[None]:
        """A future resolved when this scope is cancelled, in the running loop."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        if self._cancelled:
            future.set_result(None)
        else:
            self._waiters.append((loop, future))
        return future

    def unregister_cancellation(self, future: asyncio.Future[None]) -> None:
        self._waiters = [entry for entry in self._waiters if entry[1] is not future]

    async def wait_cancelled(self) -> None:
        await self.register_cancellation()

    def ensure_active(self) -> None:
        if self._cancelled:
            raise asyncio.CancelledError
        if self.expired:
            raise TimeoutError("the call deadline elapsed before completion")


def _resolve_threadsafe(loop: asyncio.AbstractEventLoop, future: asyncio.Future[None]) -> None:
    """Resolve a waiter from any thread; a closed loop keeps the flag only."""

    def resolve() -> None:
        if not future.done():
            future.set_result(None)

    # A closed loop cannot wake the waiter; the cancelled flag still applies.
    with contextlib.suppress(RuntimeError):
        loop.call_soon_threadsafe(resolve)


async def send_cancellable(scope: CancellationScope, start: Callable[[], Awaitable[_T]]) -> _T:
    """Await one operation, interrupting it when the scope is cancelled.

    The deadline bounds the operation through the transport timeout; this race
    makes an explicit ``cancel()`` interrupt an otherwise healthy in-flight
    request instead of waiting for it to finish.
    """
    scope.ensure_active()
    call = asyncio.ensure_future(start())
    interrupted = scope.register_cancellation()
    try:
        waiters: set[asyncio.Future[Any]] = {call, interrupted}
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if not call.done():
            call.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await call
            raise asyncio.CancelledError
        return call.result()
    finally:
        scope.unregister_cancellation(interrupted)


def resolve_timeout(request: TransportRequest, scope: CancellationScope) -> float:
    """The smaller of the request timeout and the scope's remaining deadline."""
    remaining = scope.remaining_seconds()
    if remaining is None:
        return request.timeout_seconds
    if remaining <= 0:
        raise TimeoutError("the call deadline elapsed before completion")
    return min(request.timeout_seconds, remaining)


def decode_answer(raw: bytes, content_type: str | None) -> tuple[JsonValue, bytes | None]:
    """Split one raw answer into its decoded JSON value or its raw bytes.

    A JSON media type (including structured suffixes) decodes as a value; any
    other media type travels as bytes so binary answers keep their payload.
    """
    if not raw:
        return None, None
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type and media_type not in JSON_MEDIA_TYPES and not media_type.endswith("+json"):
        return None, raw
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        raise TransportError("The transport answer is not valid JSON") from exc


def decode_array(response: TransportResponse) -> Sequence[object]:
    """Read a JSON array answer; any other shape is a protocol violation."""
    if not isinstance(response.body, list):
        raise ValidationError("The transport answer is not a JSON array")
    return response.body


def decode_binary(response: TransportResponse) -> bytes:
    """Read the raw bytes of a non-JSON answer."""
    if response.content is None:
        raise TransportError("The transport answer carried no content")
    return response.content


def _lowercased(headers: Mapping[str, str]) -> dict[str, str]:
    """Header names are case-insensitive; emit one canonical lowercase key."""
    return {name.lower(): value for name, value in headers.items()}


def build_call_headers(
    request: TransportRequest,
    context: TransportContext,
    *,
    identity: Mapping[str, str],
    protected: Collection[str] = (),
    overridable: Collection[str] = (),
) -> dict[str, str]:
    """Assemble call headers: caller headers, trace, then identity last.

    Every header name is normalized to lowercase before checking and before
    emission, so a case variant cannot smuggle a second value for the same
    header past the checks or past the server. Headers that protect the
    transport identity (the session or the delegation chain) cannot be
    supplied by any caller source — request, context identity or trace:
    overriding one is a security violation, not a merge conflict. A context
    may only replace the headers the transport declares overridable (the
    session of a delegated invocation), and trace headers never override an
    identity header because identity is applied after trace.
    """
    protected_set = {name.lower() for name in protected}
    overridable_set = {name.lower() for name in overridable}

    def reject(source: Mapping[str, str], forbidden: set[str]) -> None:
        forged = sorted(forbidden.intersection(source))
        if forged:
            raise SecurityError(
                "The caller cannot override protected transport identity headers",
                details={"forged_headers": forged},
            )

    caller = _lowercased(request.headers)
    trace = _lowercased(context.trace_headers)
    context_identity = _lowercased(context.identity_headers)
    reject(caller, protected_set)
    reject(trace, protected_set)
    reject(context_identity, protected_set - overridable_set)

    headers = caller
    headers.update(trace)
    headers.update(_lowercased(identity))
    headers.update(context_identity)
    return headers


@dataclass(slots=True)
class TransportContext:
    """Per-call context: identity headers, trace context and cancellation."""

    scope: CancellationScope = dataclass_field(default_factory=CancellationScope)
    identity_headers: Mapping[str, str] = dataclass_field(default_factory=dict)
    trace_headers: Mapping[str, str] = dataclass_field(default_factory=dict)


def require_session(context: TransportContext) -> str:
    """Read the session identity from the context; a missing one is fatal."""
    value = _lowercased(context.identity_headers).get(SESSION_HEADER.lower())
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


class GroupChannel(Protocol):
    """The composing-client surface a generated group client calls through.

    The generated ``api`` groups never import the composing client: they
    receive this channel, read its transport, its runtime API root and ask
    for a fresh per-call context that carries the caller's identity and
    cancellation scope. ``api_root`` is the server's configured API prefix
    (``OPSMESH_API_PREFIX``); generated resource paths are relative to it.
    """

    @property
    def transport(self) -> Transport: ...

    @property
    def api_root(self) -> str: ...

    def context(self) -> TransportContext: ...


__all__ = [
    "CONTENT_TYPE_HEADER",
    "DEFAULT_TIMEOUT_SECONDS",
    "JSON_MEDIA_TYPES",
    "MAX_RESPONSE_BYTES",
    "SESSION_HEADER",
    "CancellationScope",
    "GroupChannel",
    "StreamChunk",
    "Transport",
    "TransportContext",
    "TransportRequest",
    "TransportResponse",
    "build_call_headers",
    "decode_answer",
    "decode_array",
    "decode_binary",
    "require_session",
    "resolve_timeout",
    "send_cancellable",
]
