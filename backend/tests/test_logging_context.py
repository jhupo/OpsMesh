import json
import logging
import sys
from uuid import uuid4

from backend.app.core.logging import RequestContextFilter, json_log_formatter
from backend.app.core.request_context import current_log_context, log_context


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
    assert record.parent_span_id == "abcdef0123456789"
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
