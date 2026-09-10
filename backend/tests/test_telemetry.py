import subprocess
import sys
from pathlib import Path

from opentelemetry.trace import SpanKind

from backend.app.core.trace_context import (
    current_trace_context,
    telemetry_span,
    trace_context_from_headers,
)

ROOT = Path(__file__).resolve().parents[2]


def test_w3c_traceparent_is_preserved_at_the_product_boundary() -> None:
    context = trace_context_from_headers(
        {"traceparent": ("00-0123456789abcdef0123456789abcdef-abcdef0123456789-01")}
    )

    assert context.trace_id == "0123456789abcdef0123456789abcdef"
    assert context.parent_span_id == "abcdef0123456789"
    assert context.span_id != context.parent_span_id

    with telemetry_span(
        "opsmesh.test.child",
        parent=context,
        kind=SpanKind.PRODUCER,
    ) as child:
        assert child.trace_id == context.trace_id
        assert child.parent_span_id == context.span_id
        assert current_trace_context() == child
    assert current_trace_context() is None


def test_official_otel_sdk_exports_a_real_parented_span() -> None:
    script = """
import json
import logging
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from sqlalchemy import create_engine

import backend.app.telemetry.tracing as tracing
from backend.app.core.config import Settings
from backend.app.core.trace_context import TraceContext, telemetry_span

exporter = InMemorySpanExporter()
log_exporter = InMemoryLogExporter()
tracing.OTLPSpanExporter = lambda **_: exporter
tracing.OTLPLogExporter = lambda **_: log_exporter
runtime = tracing.configure_worker_telemetry(
    Settings(
        environment="test",
        service_name="opsmesh-test-worker",
        otel_exporter_otlp_endpoint="http://127.0.0.1:4317",
        otel_exporter_otlp_insecure=True,
    ),
    engine=create_engine("sqlite+pysqlite:///:memory:"),
)
parent = TraceContext(
    trace_id="0123456789abcdef0123456789abcdef",
    span_id="abcdef0123456789",
)
with telemetry_span("opsmesh.worker.test", parent=parent):
    logging.getLogger("opsmesh.test").warning("worker emitted a structured log")
assert runtime.provider is not None
assert runtime.logger_provider is not None
assert runtime.provider.force_flush(timeout_millis=5_000)
assert runtime.logger_provider.force_flush(timeout_millis=5_000)
spans = exporter.get_finished_spans()
logs = log_exporter.get_finished_logs()
assert [span.name for span in spans] == ["opsmesh.worker.test"]
assert format(spans[0].context.trace_id, "032x") == parent.trace_id
assert format(spans[0].parent.span_id, "016x") == parent.span_id
assert spans[0].resource.attributes["service.name"] == "opsmesh-test-worker"
assert len(logs) == 1
body = json.loads(logs[0].log_record.body)
assert body["message"] == "worker emitted a structured log"
assert body["trace_id"] == parent.trace_id
assert body["service_name"] == "opsmesh-test-worker"
assert format(logs[0].log_record.trace_id, "032x") == parent.trace_id
assert logs[0].resource.attributes["service.name"] == "opsmesh-test-worker"
runtime.shutdown()
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_fastapi_instrumentation_inherits_the_w3c_parent() -> None:
    script = """
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy import create_engine

import backend.app.telemetry.tracing as tracing
from backend.app.core.config import Settings
from backend.app.api.middleware import RequestContextMiddleware

exporter = InMemorySpanExporter()
tracing.OTLPSpanExporter = lambda **_: exporter
tracing.HTTPXClientInstrumentor.instrument = lambda *_, **__: None
tracing.RedisInstrumentor.instrument = lambda *_, **__: None
tracing.SQLAlchemyInstrumentor.instrument = lambda *_, **__: None
settings = Settings(
    environment="test",
    service_name="opsmesh-test-api",
    otel_exporter_otlp_endpoint="http://127.0.0.1:4317",
    otel_exporter_otlp_insecure=True,
    otel_logs_enabled=False,
)
app = FastAPI()
app.add_middleware(RequestContextMiddleware, settings=settings)

@app.get("/probe")
async def probe() -> dict[str, str]:
    return {"status": "ok"}

runtime = tracing.configure_api_telemetry(
    app,
    settings,
    engine=create_engine("sqlite+pysqlite:///:memory:"),
)
parent_trace_id = "0123456789abcdef0123456789abcdef"
parent_span_id = "abcdef0123456789"
with TestClient(app) as client:
    response = client.get(
        "/probe",
        headers={"traceparent": f"00-{parent_trace_id}-{parent_span_id}-01"},
    )
assert response.status_code == 200
assert response.headers["x-trace-id"] == parent_trace_id
assert runtime.provider is not None
assert runtime.provider.force_flush(timeout_millis=5_000)
server_spans = [span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER]
assert len(server_spans) == 1
assert format(server_spans[0].context.trace_id, "032x") == parent_trace_id
assert format(server_spans[0].parent.span_id, "016x") == parent_span_id
assert server_spans[0].resource.attributes["service.name"] == "opsmesh-test-api"
runtime.shutdown()
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
