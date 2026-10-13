import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import propagate
from opentelemetry.trace import SpanKind
from sqlalchemy import create_engine
from temporalio.contrib.opentelemetry import create_tracer_provider

from opsmesh.bootstrap.telemetry import configure_api_telemetry
from opsmesh.shared.config import Settings
from opsmesh.shared.http.middleware import RequestContextMiddleware
from opsmesh.shared.telemetry.trace_context import current_trace_metadata

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("flags", ["00", "01"])
def test_correlation_headers_remain_active_when_otel_export_is_disabled(flags: str) -> None:
    app = FastAPI()
    settings = Settings(environment="test", tracing_enabled=False)
    app.add_middleware(RequestContextMiddleware, settings=settings)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    configure_api_telemetry(app, settings, engine=engine)

    @app.get("/probe")
    async def probe() -> dict[str, str]:
        return {"status": "ok"}

    trace_id = "0123456789abcdef0123456789abcdef"
    parent_span_id = "abcdef0123456789"
    response = TestClient(app).get(
        "/probe",
        headers={
            "X-Request-ID": "request-no-exporter",
            "traceparent": f"00-{trace_id}-{parent_span_id}-{flags}",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "request-no-exporter"
    assert response.headers["x-trace-id"] == trace_id
    assert response.headers["x-parent-span-id"] == parent_span_id
    assert len(response.headers["x-span-id"]) == 16
    assert response.headers["traceparent"].endswith(f"-{flags}")
    fresh = TestClient(app).get("/probe")
    another = TestClient(app).get("/probe")
    assert len(fresh.headers["x-trace-id"]) == 32
    assert len(fresh.headers["x-span-id"]) == 16
    assert fresh.headers["x-trace-id"] != another.headers["x-trace-id"]
    assert fresh.headers["traceparent"].endswith("-00")
    engine.dispose()


@pytest.mark.parametrize("flags", ["00", "01"])
def test_native_context_preserves_parent_sampling_and_nested_identity(flags: str) -> None:
    carrier = {"traceparent": f"00-0123456789abcdef0123456789abcdef-abcdef0123456789-{flags}"}
    provider = create_tracer_provider()
    tracer = provider.get_tracer(__name__)
    try:
        with tracer.start_as_current_span("parent", context=propagate.extract(carrier)) as parent:
            parent_id = current_trace_metadata()["span_id"]
            with tracer.start_as_current_span("child", kind=SpanKind.PRODUCER) as child:
                metadata = current_trace_metadata()
                assert metadata["trace_id"] == "0123456789abcdef0123456789abcdef"
                assert metadata["span_id"] != parent_id
                assert child.get_span_context().trace_flags == parent.get_span_context().trace_flags
                propagated: dict[str, str] = {}
                propagate.inject(propagated)
                assert propagated["traceparent"].endswith(f"-{flags}")
            assert current_trace_metadata()["span_id"] == parent_id
        assert current_trace_metadata() == {}
    finally:
        provider.shutdown()


def test_official_otel_sdk_exports_a_real_parented_span() -> None:
    script = """
import json
import logging
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy import create_engine

import opsmesh.shared.telemetry.tracing as tracing
from opsmesh.bootstrap.telemetry import configure_worker_telemetry
from opsmesh.shared.config import Settings
from opentelemetry import propagate

exporter = InMemorySpanExporter()
log_exporter = InMemoryLogExporter()
tracing.OTLPSpanExporter = lambda **_: exporter
tracing.OTLPLogExporter = lambda **_: log_exporter
runtime = configure_worker_telemetry(
    Settings(
        environment="test",
        service_name="opsmesh-test-worker",
        otel_exporter_otlp_endpoint="http://127.0.0.1:4317",
        otel_exporter_otlp_insecure=True,
    ),
    engine=create_engine("sqlite+pysqlite:///:memory:"),
)
parent_trace_id = "0123456789abcdef0123456789abcdef"
parent_span_id = "abcdef0123456789"
tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span(
    "opsmesh.worker.unsampled",
    context=propagate.extract({"traceparent": f"00-{parent_trace_id}-{parent_span_id}-00"}),
):
    assert not trace.get_current_span().is_recording()
with tracer.start_as_current_span(
    "opsmesh.worker.test",
    context=propagate.extract({"traceparent": f"00-{parent_trace_id}-{parent_span_id}-01"}),
):
    span = trace.get_current_span()
    span.set_attributes({
        "input.value": "private-prompt-canary",
        "tool.parameters": "private-tool-schema-canary",
        "http.url": "https://provider.invalid?api_key=sk-private-canary",
        "llm.token_count.total": 42,
    })
    span.add_event("exception", {"exception.message": "private-error-canary"})
    span.set_status(Status(StatusCode.ERROR, "private-status-canary"))
    logging.getLogger("opsmesh.test").warning("worker emitted a structured log")
assert runtime.provider is not None
assert runtime.logger_provider is not None
assert runtime.provider.force_flush(timeout_millis=5_000)
assert runtime.logger_provider.force_flush(timeout_millis=5_000)
spans = exporter.get_finished_spans()
logs = log_exporter.get_finished_logs()
assert [span.name for span in spans] == ["opsmesh.worker.test"]
assert format(spans[0].context.trace_id, "032x") == parent_trace_id
assert format(spans[0].parent.span_id, "016x") == parent_span_id
assert spans[0].resource.attributes["service.name"] == "opsmesh-test-worker"
assert spans[0].attributes == {"llm.token_count.total": 42}
assert spans[0].events[0].attributes == {}
assert spans[0].status.status_code == StatusCode.ERROR
assert spans[0].status.description is None
assert len(logs) == 1
body = json.loads(logs[0].log_record.body)
assert body["message"] == "worker emitted a structured log"
assert body["trace_id"] == parent_trace_id
assert body["service_name"] == "opsmesh-test-worker"
assert format(logs[0].log_record.trace_id, "032x") == parent_trace_id
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
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind
from sqlalchemy import create_engine
from temporalio.contrib.opentelemetry import create_tracer_provider

from opsmesh.bootstrap.telemetry import configure_api_telemetry
from sqlalchemy import create_engine

import opsmesh.shared.telemetry.tracing as tracing
from opsmesh.bootstrap.telemetry import configure_api_telemetry
from opsmesh.shared.config import Settings
from opsmesh.shared.http.middleware import RequestContextMiddleware

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

runtime = configure_api_telemetry(
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
    unsampled = client.get(
        "/probe",
        headers={"traceparent": f"00-{parent_trace_id}-{parent_span_id}-00"},
    )
assert unsampled.status_code == 200
assert unsampled.headers["traceparent"].endswith("-00")
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
