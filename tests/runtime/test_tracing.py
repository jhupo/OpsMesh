"""Use the real OTel provider and SDK callbacks without any remote exporter."""

from types import SimpleNamespace

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from opsmesh.modules.runtime.internal.backends.openai_agents.tracing import (
    OpenTelemetryTraceProcessor,
)


def test_sdk_spans_preserve_parentage_without_prompt_data():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    processor = OpenTelemetryTraceProcessor(provider)
    trace = SimpleNamespace(
        trace_id="trace", export=lambda: {"metadata": {"run_id": "run"}, "group_id": "session"}
    )
    span = SimpleNamespace(
        trace_id="trace",
        span_id="span",
        parent_id=None,
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        error=None,
        span_data=SimpleNamespace(type="generation", input="secret prompt"),
    )
    processor.on_trace_start(trace)
    processor.on_span_start(span)
    processor.on_span_end(span)
    processor.on_trace_end(trace)
    exported = exporter.get_finished_spans()
    child, root = exported
    assert child.parent.span_id == root.context.span_id
    assert root.attributes["opsmesh.run_id"] == "run"
    assert "secret prompt" not in str(exported)
    processor.shutdown()
    provider.shutdown()
