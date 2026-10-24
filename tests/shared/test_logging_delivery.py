"""Standard logging reaches filtered bounded sinks directly, without a bus."""

import linecache
import logging
from queue import Queue

from opentelemetry.instrumentation.logging.handler import LoggingHandler

from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.telemetry.logging import BoundedLogHandler, RequestContextFilter


def test_standard_logging_reaches_both_sinks_once(monkeypatch):
    queue = Queue(maxsize=2)
    bounded = BoundedLogHandler(queue)
    exported = []
    otlp = LoggingHandler()
    monkeypatch.setattr(otlp, "emit", lambda record: exported.append(record.getMessage()))
    for sink in (bounded, otlp):
        sink.addFilter(
            RequestContextFilter(
                service_name="test", environment="test", redaction=PLATFORM_REDACTION
            )
        )
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [bounded, otlp])
    root.handle(logging.LogRecord("test", logging.WARNING, "", 0, "password=private", (), None))
    assert queue.get_nowait().getMessage() == exported[0]
    assert len(exported) == 1 and "private" not in exported[0]
    assert queue.empty()


def test_oversized_and_exception_logs_are_safe_and_full_queue_does_not_block(monkeypatch):
    queue = Queue(maxsize=1)
    bounded = BoundedLogHandler(queue)
    bounded.addFilter(
        RequestContextFilter(service_name="test", environment="test", redaction=PLATFORM_REDACTION)
    )
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [bounded])
    root.handle(logging.LogRecord("test", logging.ERROR, "", 0, "x" * 20000, (), None))
    assert queue.get_nowait().getMessage() == "Log record exceeded size limit"
    try:
        raise ValueError("password=private")
    except ValueError as exc:
        root.handle(
            logging.LogRecord(
                "test", logging.ERROR, "", 0, "failed", (), (type(exc), exc, exc.__traceback__)
            )
        )
    root.handle(logging.LogRecord("test", logging.ERROR, "", 0, "dropped", (), None))
    record = queue.get_nowait()
    assert "private" not in record.getMessage() and queue.empty()


def test_exception_chain_and_group_logs_never_read_source(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Exception logging must not read source files")

    try:
        try:
            raise ValueError("original failure")
        except ValueError as cause:
            raise ExceptionGroup("group", [RuntimeError("nested")]) from cause
    except ExceptionGroup as error:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            "",
            0,
            "failed",
            (),
            (type(error), error, error.__traceback__),
        )
    with monkeypatch.context() as patch:
        patch.setattr(linecache, "getline", forbidden)
        filtered = RequestContextFilter(redaction=PLATFORM_REDACTION).filter(record)
    assert filtered
    assert "private" not in record.exc_text
    assert "ValueError" in record.exc_text
    assert "RuntimeError: nested" in record.exc_text
    assert "test_exception_chain_and_group_logs_never_read_source" in record.exc_text
