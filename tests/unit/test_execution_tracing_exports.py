"""Observe actual native OTLP results without collecting sensitive payloads."""

import asyncio
import json
from datetime import datetime

import pytest

from opsmesh.plugins.execution._tracing import TracingSupport
from opsmesh.plugins.execution._tracing.exporter import ExportDiagnostics
from opsmesh.plugins.execution.config import LangfuseSettings, TracingSettings

pytest.importorskip("opentelemetry.sdk.trace.export")


class NativeExporter:
    def __init__(self):
        from opentelemetry.sdk.trace.export import SpanExportResult

        self.result = SpanExportResult.SUCCESS
        self.error = None
        self.exports = 0
        self.shutdowns = 0
        self.flushes = []

    def export(self, spans):
        self.exports += 1
        if self.error is not None:
            raise self.error
        return self.result

    def shutdown(self):
        self.shutdowns += 1

    def force_flush(self, timeout_millis=30000):
        self.flushes.append(timeout_millis)
        return True


def settings():
    return TracingSettings(
        destination="langfuse",
        langfuse=LangfuseSettings(
            base_url="https://private-host.invalid",
            public_key_ref="EXPORT_PUBLIC",
            secret_key_ref="EXPORT_SECRET",
        ),
    )


@pytest.fixture
def native_exporter(monkeypatch):
    pytest.importorskip("langfuse")
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    monkeypatch.setenv("EXPORT_PUBLIC", "private-public-key")
    monkeypatch.setenv("EXPORT_SECRET", "private-secret-key")
    native = NativeExporter()
    monkeypatch.setattr(
        trace_exporter, "OTLPSpanExporter", lambda **options: native
    )
    return native


def test_export_diagnostics_track_completed_batches_and_safe_error_codes():
    from opentelemetry.sdk.trace.export import SpanExportResult

    native = NativeExporter()
    exporter = ExportDiagnostics(native)
    assert exporter.snapshot() == {
        "last_result": None,
        "successful_batches": 0,
        "failed_batches": 0,
        "last_export_at": None,
        "last_error": None,
    }
    assert exporter.export([]) is SpanExportResult.SUCCESS
    native.result = SpanExportResult.FAILURE
    assert exporter.export([]) is SpanExportResult.FAILURE
    failure = exporter.snapshot()
    assert failure["last_result"] == "failure"
    assert failure["last_error"] == "export_failed"
    assert failure["successful_batches"] == 1
    assert failure["failed_batches"] == 1
    assert datetime.fromisoformat(failure["last_export_at"]).tzinfo is not None
    native.error = RuntimeError(
        "private model content and Authorization secret"
    )
    assert exporter.export([]) is SpanExportResult.FAILURE
    assert exporter.snapshot()["last_error"] == "export_exception"
    assert exporter.snapshot()["failed_batches"] == 2
    native.error = None
    native.result = SpanExportResult.SUCCESS
    exporter.export([])
    assert exporter.snapshot()["last_error"] is None
    assert "private" not in json.dumps(exporter.snapshot())
    exporter.force_flush(timeout_millis=123)
    exporter.shutdown()
    assert native.flushes == [123] and native.shutdowns == 1


@pytest.mark.anyio
async def test_native_batch_enqueue_is_not_counted_as_a_successful_export(
    native_exporter,
    monkeypatch,
):
    from agents.tracing import custom_span

    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", "60000")
    support = TracingSupport(settings())
    support.install()
    try:
        with support.scope(support.prepare(None)):
            with custom_span("private custom data"):
                pass
        snapshot = support.diagnostics()["langfuse"]["exporter"]
        assert native_exporter.exports == 0
        assert snapshot["last_result"] is None
        assert snapshot["successful_batches"] == 0
        assert await asyncio.to_thread(support._bridge._provider.force_flush)
        snapshot = support.diagnostics()["langfuse"]["exporter"]
        assert snapshot["last_result"] == "success"
        assert snapshot["successful_batches"] == native_exporter.exports
        assert "private" not in json.dumps(support.diagnostics())
    finally:
        await support.shutdown()
    final = support.diagnostics()
    assert final["installed"] is False
    assert final["langfuse"]["provider_active"] is False
    assert final["langfuse"]["exporter"]["last_result"] == "success"
    final["langfuse"]["exporter"]["last_result"] = "mutated"
    assert (
        support.diagnostics()["langfuse"]["exporter"]["last_result"]
        == "success"
    )
    assert native_exporter.shutdowns == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["result", "exception"])
async def test_failed_native_export_preserves_result_without_retrying_model(
    native_exporter,
    monkeypatch,
    failure,
    caplog,
):
    from agents import Agent, Runner
    from agents.testing import ScriptedModel
    from agents.testing.model import ModelStep, assistant_message
    from opentelemetry.sdk.trace import export

    if failure == "result":
        native_exporter.result = export.SpanExportResult.FAILURE
    else:
        native_exporter.error = RuntimeError(
            "private credentials and model input"
        )
    monkeypatch.setattr(
        export,
        "BatchSpanProcessor",
        lambda exporter, **options: export.SimpleSpanProcessor(exporter),
    )
    support = TracingSupport(settings())
    support.install()
    model = ScriptedModel([ModelStep(output=[assistant_message("answer")])])
    try:
        config = support.prepare(None)
        with support.scope(config):
            result = await Runner.run(
                Agent(name="test", model=model), "hello", run_config=config
            )
        assert result.final_output == "answer"
        assert len(model.calls) == 1
        diagnostics = support.diagnostics()["langfuse"]["exporter"]
        assert diagnostics["last_result"] == "failure"
        assert diagnostics["failed_batches"] == native_exporter.exports > 0
        assert "private" not in json.dumps(support.diagnostics())
        assert "private" not in caplog.text
    finally:
        await support.shutdown()
