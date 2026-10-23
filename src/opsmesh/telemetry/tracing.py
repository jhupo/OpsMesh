"""Native spans and trace identities without application-specific attributes."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import propagate, trace
from opentelemetry.trace import Span, SpanKind
from opentelemetry.util.types import Attributes


def current_trace_context() -> dict[str, str]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


@contextmanager
def start_span(
    name: str,
    *,
    scope: str,
    attributes: Attributes = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[Span]:
    with trace.get_tracer(scope).start_as_current_span(
        name, kind=kind, attributes=attributes
    ) as span:
        yield span


def parent_span_id(carrier: Mapping[str, str]) -> str | None:
    context = trace.get_current_span(propagate.extract(dict(carrier))).get_span_context()
    return format(context.span_id, "016x") if context.is_valid else None
