"""Own temporary logging context for test scenarios."""

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import context, propagate

from opsmesh.shared.telemetry.request_context import reset_log_context, set_log_context


@contextmanager
def log_context(**values: object) -> Iterator[None]:
    native_token = None
    if trace_id := values.get("trace_id"):
        span_id = values.get("span_id", "abcdef0123456789")
        native_token = context.attach(
            propagate.extract({"traceparent": f"00-{trace_id}-{span_id}-01"})
        )
    tokens = set_log_context(**values)
    try:
        yield
    finally:
        reset_log_context(tokens)
        if native_token is not None:
            context.detach(native_token)
