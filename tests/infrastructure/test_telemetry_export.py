"""Actual OTLP HTTP export and bounded operational metrics."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics
from opsmesh.infrastructure.telemetry.tracing import ApplicationTraceExporter, current_trace_carrier


def test_otlp_http_exports_parent_link_without_user_payload(tmp_path: Path) -> None:
    batches: list[Any] = []
    received = Event()

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/traces"
            assert self.headers["Authorization"] == "Bearer local-test-only"
            batch = ExportTraceServiceRequest()
            batch.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
            batches.append(batch)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            received.set()

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    exporter = ApplicationTraceExporter(
        tmp_path,
        endpoint=f"http://127.0.0.1:{server.server_port}/v1/traces",
        headers={"Authorization": "Bearer local-test-only"},
        timeout_seconds=2,
    )
    try:
        incoming = {"traceparent": "00-0123456789abcdef0123456789abcdef-1234567890abcdef-01"}
        with exporter.span("http.request", carrier=incoming):
            carrier = current_trace_carrier()
            with exporter.span("worker.resume", carrier=carrier):
                pass
        assert exporter.provider.force_flush(timeout_millis=2000)
        assert received.wait(1)
        spans = [
            span
            for batch in batches
            for resource in batch.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]
        parent = next(span for span in spans if span.name == "http.request")
        child = next(span for span in spans if span.name == "worker.resume")
        assert parent.trace_id.hex() == "0123456789abcdef0123456789abcdef"
        assert parent.parent_span_id.hex() == "1234567890abcdef"
        assert child.parent_span_id == parent.span_id
        assert all(not span.events for span in spans)
    finally:
        assert exporter.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_export_shutdown_has_a_deadline(tmp_path: Path) -> None:
    exporter = ApplicationTraceExporter(tmp_path, timeout_seconds=0.02)
    finish = Event()
    exited = Event()
    original = exporter.provider.force_flush

    def slow_flush(**kwargs: Any) -> bool:
        finish.wait(2)
        exited.set()
        return True

    exporter.provider.force_flush = slow_flush
    started = monotonic()
    try:
        assert not exporter.close()
        assert monotonic() - started < 0.7
    finally:
        finish.set()
        assert exited.wait(1)
        exporter.provider.force_flush = original


def test_metrics_histograms_are_bounded_and_have_prometheus_buckets() -> None:
    metrics = PlatformMetrics()
    metrics.observe("http_duration_seconds", 0.02)
    metrics.observe("http_duration_seconds", 2)
    metrics.gauge("active_runs", 3)
    rendered = metrics.render()
    assert 'opsmesh_http_duration_seconds_bucket{le="0.025"} 1' in rendered
    assert 'opsmesh_http_duration_seconds_bucket{le="+Inf"} 2' in rendered
    assert "opsmesh_http_duration_seconds_sum 2.02" in rendered
    assert "opsmesh_active_runs 3" in rendered
