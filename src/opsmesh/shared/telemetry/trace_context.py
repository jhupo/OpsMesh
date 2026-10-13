"""Project the active SDK context into business records; OTel owns the call graph."""

from opentelemetry import trace

TRACE_ID_HEADER = "X-Trace-ID"
SPAN_ID_HEADER = "X-Span-ID"
PARENT_SPAN_ID_HEADER = "X-Parent-Span-ID"
TRACEPARENT_HEADER = "traceparent"


def current_trace_metadata() -> dict[str, str]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


def with_current_trace_metadata(metadata: dict[str, object] | None = None) -> dict[str, object]:
    return dict(metadata or {}) | current_trace_metadata()
