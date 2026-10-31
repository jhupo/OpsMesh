"""Bridge SDK lifecycle spans to an application-owned OpenTelemetry provider."""

import logging
from datetime import datetime
from threading import RLock
from typing import Any

from agents import set_trace_processors
from agents.tracing import TracingProcessor
from opentelemetry import trace as otel
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)


def timestamp_ns(value: str | None) -> int | None:
    return int(datetime.fromisoformat(value).timestamp() * 1_000_000_000) if value else None


class OpenTelemetryTraceProcessor(TracingProcessor):
    """Preserve parent relationships and IDs while excluding prompts, tools' data and outputs."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.tracer = provider.get_tracer("opsmesh.openai_agents")
        self._spans: dict[str, Any] = {}
        self._lock = RLock()

    def on_trace_start(self, trace: Any) -> None:
        try:
            data = trace.export() or {}
            metadata = data.get("metadata") or {}
            attributes = {"agents.trace_id": trace.trace_id}
            if metadata.get("run_id"):
                attributes["opsmesh.run_id"] = metadata["run_id"]
            if data.get("group_id"):
                attributes["opsmesh.session_id"] = data["group_id"]
            with self._lock:
                self._spans[trace.trace_id] = self.tracer.start_span(
                    "agent.run",
                    attributes=attributes,
                    context=TraceContextTextMapPropagator().extract(metadata)
                    if metadata.get("traceparent")
                    else None,
                )
        except Exception:
            logger.exception("Failed to start SDK trace bridge")

    def on_trace_end(self, trace: Any) -> None:
        self._finish(trace.trace_id)

    def on_span_start(self, span: Any) -> None:
        try:
            with self._lock:
                parent = self._spans.get(span.parent_id or span.trace_id)
                context = otel.set_span_in_context(parent) if parent is not None else Context()
                self._spans[span.span_id] = self.tracer.start_span(
                    f"agent.{span.span_data.type}",
                    context=context,
                    start_time=timestamp_ns(span.started_at),
                    attributes={
                        "agents.trace_id": span.trace_id,
                        "agents.span_id": span.span_id,
                        "agents.operation": span.span_data.type,
                    },
                )
        except Exception:
            logger.exception("Failed to start SDK span bridge")

    def on_span_end(self, span: Any) -> None:
        self._finish(span.span_id, end_time=timestamp_ns(span.ended_at), failed=bool(span.error))

    def _finish(self, identity: str, *, end_time: int | None = None, failed: bool = False) -> None:
        try:
            with self._lock:
                span = self._spans.pop(identity, None)
            if span is not None:
                if failed:
                    span.set_status(otel.StatusCode.ERROR)
                span.end(end_time=end_time)
        except Exception:
            logger.exception("Failed to finish SDK span bridge")

    def force_flush(self) -> None:
        self.provider.force_flush()

    def shutdown(self) -> None:
        # The application owns provider shutdown; SDK shutdown only drains this adapter.
        with self._lock:
            identities = list(self._spans)
        for identity in identities:
            self._finish(identity, failed=True)
        self.force_flush()


def configure_sdk_tracing(provider: Any) -> OpenTelemetryTraceProcessor:
    """Call once at bootstrap; replaces the SDK's default remote export processor."""
    processor = OpenTelemetryTraceProcessor(provider)
    set_trace_processors([processor])
    return processor
