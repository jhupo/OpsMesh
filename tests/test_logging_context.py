import json
import logging
import sys
from queue import Queue
from uuid import uuid4

from temporalio.contrib.opentelemetry import create_tracer_provider

from opsmesh.shared.telemetry.logging import (
    BoundedLogHandler,
    RequestContextFilter,
    json_log_formatter,
)
from opsmesh.shared.telemetry.request_context import current_log_context
from tests.fixtures.log_context import log_context


def test_log_context_uses_native_activity_span_and_restores_request_scope() -> None:
    provider = create_tracer_provider()
    try:
        with log_context(
            request_id="api-request",
            workspace_id="tenant-a",
            trace_id="0123456789abcdef0123456789abcdef",
            span_id="0123456789abcdef",
        ):
            with provider.get_tracer("native-activity-log").start_as_current_span("model") as span:
                active = current_log_context()
                assert active["request_id"] == "api-request"
                assert active["workspace_id"] == "tenant-a"
                assert active["trace_id"] == format(span.get_span_context().trace_id, "032x")
                assert active["span_id"] == format(span.get_span_context().span_id, "016x")
                assert active["parent_span_id"] is None
            assert current_log_context()["trace_id"] == "0123456789abcdef0123456789abcdef"
            assert current_log_context()["span_id"] == "0123456789abcdef"
        assert current_log_context()["workspace_id"] is None
    finally:
        provider.shutdown()


def test_log_context_filter_adds_standard_fields_to_records() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    run_id = uuid4()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    with log_context(
        request_id="req-1",
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="0123456789abcdef",
        parent_span_id="abcdef0123456789",
        workspace_id=workspace_id,
        user_id=user_id,
        run_id=run_id,
        worker_id="worker-1",
    ):
        accepted = RequestContextFilter().filter(record)

    assert accepted is True
    assert record.request_id == "req-1"
    assert record.trace_id == "0123456789abcdef0123456789abcdef"
    assert record.span_id == "0123456789abcdef"
    assert record.parent_span_id is None
    assert record.workspace_id == str(workspace_id)
    assert record.user_id == str(user_id)
    assert record.task_id is None
    assert record.run_id == str(run_id)
    assert record.worker_id == "worker-1"


def test_log_context_resets_nested_values() -> None:
    assert current_log_context()["workspace_id"] is None

    with log_context(workspace_id="outer", user_id="u1"):
        assert current_log_context()["workspace_id"] == "outer"
        assert current_log_context()["user_id"] == "u1"
        with log_context(workspace_id="inner", task_id="task-1"):
            assert current_log_context()["workspace_id"] == "inner"
            assert current_log_context()["task_id"] == "task-1"
        assert current_log_context()["workspace_id"] == "outer"
        assert current_log_context()["task_id"] is None

    assert current_log_context()["workspace_id"] is None
    assert current_log_context()["user_id"] is None


def test_log_queue_drops_at_capacity_without_synchronous_fallback():
    records = Queue(maxsize=1)
    handler = BoundedLogHandler(records)
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    handler.enqueue(record)
    handler.enqueue(record)
    assert records.qsize() == 1


def test_log_filter_bounds_payload_and_retains_error_identity():
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "Failure", (), None)
    record.payload = "x" * 100_000
    record.error_id = "error-1"
    RequestContextFilter().filter(record)
    rendered = json_log_formatter().format(record)
    assert len(rendered.encode()) < 16_384
    assert json.loads(rendered)["error_id"] == "error-1"
    assert "payload" not in record.__dict__


def test_log_filter_redacts_formatted_messages_and_structured_extras() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="authorization=%s",
        args=("Bearer secret-token",),
        exc_info=None,
    )
    record.payload = {"api_key": "sk-log-secret", "safe": "visible"}

    accepted = RequestContextFilter(
        service_name="opsmesh-api",
        environment="test",
    ).filter(record)

    assert accepted is True
    assert record.getMessage() == "[redacted]"
    assert record.payload == {"api_key": "[redacted]", "safe": "visible"}
    assert record.service_name == "opsmesh-api"
    assert record.environment == "test"
    assert "secret-token" not in str(record.__dict__)
    assert "sk-log-secret" not in str(record.__dict__)


def test_log_filter_redacts_sensitive_exception_tracebacks() -> None:
    try:
        raise RuntimeError("provider rejected api_key=sk-exception-secret")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Provider request failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    RequestContextFilter(service_name="opsmesh-api", environment="test").filter(record)
    rendered = json.loads(json_log_formatter().format(record))

    assert record.exc_info is None
    assert record.exc_text == "[redacted]"
    assert "[redacted]" in str(rendered)
    assert "sk-exception-secret" not in str(record.__dict__)
    assert "sk-exception-secret" not in str(rendered)
