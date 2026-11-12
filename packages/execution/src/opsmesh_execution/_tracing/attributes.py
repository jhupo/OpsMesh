"""Attach Langfuse dimensions to native OpenInference spans."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from langfuse import propagate_attributes
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span, SpanProcessor
from opentelemetry.trace import use_span

from .context import TraceContext


@dataclass(frozen=True, slots=True)
class TraceAttributes:
    """Keep observation attributes local to the current execution task."""

    context: TraceContext
    sdk_trace_id: str | None
    plugin_id: str | None
    environment: str | None

    @contextmanager
    def propagate(self) -> Iterator[None]:
        """Use the public Langfuse helper to annotate the active span."""
        metadata = {}
        if self.sdk_trace_id is not None:
            metadata["sdk_trace_id"] = self.sdk_trace_id
        if self.plugin_id is not None:
            metadata["plugin_id"] = self.plugin_id
        with propagate_attributes(
            user_id=self.context.user_id,
            session_id=self.context.session_id,
            tags=list(self.context.tags),
            metadata=metadata,
            environment=self.environment,
        ):
            yield


_attributes: ContextVar[TraceAttributes | None] = ContextVar(
    "opsmesh_trace_attributes", default=None
)


@contextmanager
def attribute_scope(attributes: TraceAttributes) -> Iterator[None]:
    """Propagate only within this execution, including inherited async tasks."""
    token = _attributes.set(attributes)
    try:
        with attributes.propagate():
            yield
    finally:
        _attributes.reset(token)


class TraceAttributeProcessor(SpanProcessor):
    """Apply public Langfuse propagation to every native SDK observation."""

    def on_start(
        self, span: Span, parent_context: Context | None = None
    ) -> None:
        """Annotate the newly created span without changing its identity."""
        attributes = _attributes.get()
        if attributes is None:
            return
        with use_span(span, end_on_exit=False), attributes.propagate():
            pass
