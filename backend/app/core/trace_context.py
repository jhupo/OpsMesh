from __future__ import annotations

import re
import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

TRACE_ID_HEADER = "X-Trace-ID"
SPAN_ID_HEADER = "X-Span-ID"
PARENT_SPAN_ID_HEADER = "X-Parent-Span-ID"
TRACEPARENT_HEADER = "traceparent"

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
span_id_var: ContextVar[str | None] = ContextVar("span_id", default=None)
parent_span_id_var: ContextVar[str | None] = ContextVar("parent_span_id", default=None)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    def child(self) -> TraceContext:
        return TraceContext(
            trace_id=self.trace_id,
            span_id=new_span_id(),
            parent_span_id=self.span_id,
        )

    def metadata(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
        }


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def new_trace_context() -> TraceContext:
    return TraceContext(trace_id=new_trace_id(), span_id=new_span_id())


def child_trace_context(parent: TraceContext | None = None) -> TraceContext:
    parent = parent or current_trace_context()
    return parent.child() if parent is not None else new_trace_context()


def trace_context_from_headers(headers: Mapping[str, str]) -> TraceContext:
    traceparent_context = _trace_context_from_traceparent(headers.get(TRACEPARENT_HEADER))
    if traceparent_context is not None:
        return traceparent_context

    trace_id = _normalize_trace_id(headers.get(TRACE_ID_HEADER))
    parent_span_id = _normalize_span_id(headers.get(SPAN_ID_HEADER))
    if trace_id is None:
        return new_trace_context()
    return TraceContext(
        trace_id=trace_id,
        span_id=new_span_id(),
        parent_span_id=parent_span_id,
    )


def trace_context_from_metadata(metadata: Mapping[str, object]) -> TraceContext | None:
    trace_id = _normalize_trace_id(_string_or_none(metadata.get("trace_id")))
    span_id = _normalize_span_id(_string_or_none(metadata.get("span_id")))
    parent_span_id = _normalize_span_id(_string_or_none(metadata.get("parent_span_id")))
    if trace_id is None or span_id is None:
        return None
    return TraceContext(trace_id=trace_id, span_id=span_id, parent_span_id=parent_span_id)


def current_trace_context() -> TraceContext | None:
    trace_id = trace_id_var.get()
    span_id = span_id_var.get()
    if trace_id is None or span_id is None:
        return None
    return TraceContext(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id_var.get(),
    )


def current_trace_metadata() -> dict[str, object]:
    context = current_trace_context()
    return context.metadata() if context is not None else {}


def with_current_trace_metadata(metadata: dict[str, object] | None = None) -> dict[str, object]:
    merged = dict(metadata or {})
    merged.update(current_trace_metadata())
    return merged


def set_trace_context(context: TraceContext) -> dict[str, Token[str | None]]:
    tokens = {
        "trace_id": trace_id_var.set(context.trace_id),
        "span_id": span_id_var.set(context.span_id),
    }
    if context.parent_span_id is not None:
        tokens["parent_span_id"] = parent_span_id_var.set(context.parent_span_id)
    else:
        tokens["parent_span_id"] = parent_span_id_var.set(None)
    return tokens


def reset_trace_context(tokens: dict[str, Token[str | None]]) -> None:
    for field, token in reversed(tokens.items()):
        if field == "trace_id":
            trace_id_var.reset(token)
        elif field == "span_id":
            span_id_var.reset(token)
        elif field == "parent_span_id":
            parent_span_id_var.reset(token)


@contextmanager
def trace_context(context: TraceContext) -> Iterator[None]:
    tokens = set_trace_context(context)
    try:
        yield
    finally:
        reset_trace_context(tokens)


def traceparent_header(context: TraceContext) -> str:
    return f"00-{context.trace_id}-{context.span_id}-01"


def _trace_context_from_traceparent(value: str | None) -> TraceContext | None:
    if value is None:
        return None
    parts = value.strip().lower().split("-")
    if len(parts) != 4:
        return None
    version, trace_id, parent_span_id, _flags = parts
    if version != "00":
        return None
    trace_id = _normalize_trace_id(trace_id)
    parent_span_id = _normalize_span_id(parent_span_id)
    if trace_id is None or parent_span_id is None:
        return None
    return TraceContext(
        trace_id=trace_id,
        span_id=new_span_id(),
        parent_span_id=parent_span_id,
    )


def _normalize_trace_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not _TRACE_ID_RE.fullmatch(normalized) or normalized == "0" * 32:
        return None
    return normalized


def _normalize_span_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not _SPAN_ID_RE.fullmatch(normalized) or normalized == "0" * 16:
        return None
    return normalized


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
