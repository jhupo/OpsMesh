"""Context-local log attributes; callers own the names of their business fields."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token

from opentelemetry import context, propagate

from opsmesh.telemetry.tracing import current_trace_context

LOG_CONTEXT_FIELDS = ("request_id", "trace_id", "span_id")
_context: ContextVar[dict[str, str | None] | None] = ContextVar("log_context", default=None)
_TRACE_FIELDS = frozenset({"trace_id", "span_id", "parent_span_id"})


def current_log_context() -> dict[str, str | None]:
    return dict.fromkeys(LOG_CONTEXT_FIELDS) | (_context.get() or {}) | current_trace_context()


def set_log_context(
    *, replace: bool = False, **values: object
) -> Token[dict[str, str | None] | None]:
    attributes = {} if replace else dict(_context.get() or {})
    attributes.update(
        (field, str(value) if value is not None else None)
        for field, value in values.items()
        if field not in _TRACE_FIELDS
    )
    return _context.set(attributes)


def reset_log_context(token: Token[dict[str, str | None] | None]) -> None:
    _context.reset(token)


@contextmanager
def bind_log_context(*, replace: bool = False, **values: object) -> Iterator[None]:
    token = set_log_context(replace=replace, **values)
    try:
        yield
    finally:
        reset_log_context(token)


def capture_trace_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def restore_trace_context(carrier: Mapping[str, str]) -> Iterator[None]:
    # An empty carrier starts a fresh boundary rather than inheriting another request.
    token = context.attach(propagate.extract(dict(carrier)) if carrier else context.Context())
    try:
        yield
    finally:
        context.detach(token)
