"""Structured logging with a bounded queue, redaction and exit-time draining."""

import json
import logging
import sys
from logging.handlers import QueueHandler, QueueListener
from queue import Full, Queue
from traceback import FrameSummary, StackSummary, TracebackException
from typing import cast

from opentelemetry.instrumentation.logging import LoggingInstrumentor
from pythonjsonlogger.json import JsonFormatter

from opsmesh.shared.security.redaction import (
    RedactionRules,
    redact_sensitive_payload,
    redact_sensitive_payload_item,
)
from opsmesh.telemetry.context import LOG_CONTEXT_FIELDS, current_log_context
from opsmesh.telemetry.metrics import increment_counter

_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
_MAX_LOG_RECORD_BYTES = 16_384
_listener: QueueListener | None = None


class BoundedLogHandler(QueueHandler):
    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except Full:
            increment_counter(
                "opsmesh_log_records_dropped_total",
                labels={"sink": "stdout"},
                help_text="Service log records dropped at queue capacity.",
            )


class DrainingQueueListener(QueueListener):
    def enqueue_sentinel(self) -> None:
        # Shutdown can wait for the consumer; request producers never block.
        cast(Queue[logging.LogRecord | None], self.queue).put(None)


def shutdown_logging() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


class RequestContextFilter(logging.Filter):
    def __init__(
        self,
        *,
        redaction: RedactionRules,
        service_name: str | None = None,
        environment: str | None = None,
    ) -> None:
        super().__init__()
        self._redaction = redaction
        self._service_name = service_name
        self._environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        for field, value in current_log_context().items():
            if field in _STANDARD_LOG_RECORD_FIELDS:
                continue
            if value is not None or not hasattr(record, field):
                setattr(record, field, value)
        record.service_name = self._service_name
        record.environment = self._environment
        try:
            rendered_message = record.getMessage()
        except (TypeError, ValueError):
            rendered_message = str(record.msg)
        record.msg = redact_sensitive_payload_item(rendered_message, rules=self._redaction)
        record.args = ()
        if record.exc_info is not None:
            trace = TracebackException(*record.exc_info, lookup_lines=False)
            pending = [trace]
            while pending:
                item = pending.pop()
                # Frozen loaders may read files, which is forbidden in Workflow code.
                item.stack = StackSummary.from_list(
                    FrameSummary(frame.filename, frame.lineno, frame.name, line="")
                    for frame in item.stack
                )
                pending.extend(
                    child for child in (item.__cause__, item.__context__) if child is not None
                )
                pending.extend(item.exceptions or [])
            exception_text = "".join(trace.format())
            redacted_exception = redact_sensitive_payload_item(
                exception_text, rules=self._redaction
            )
            record.exc_info = None
            record.exc_text = str(redacted_exception)
        if record.stack_info is not None:
            record.stack_info = str(
                redact_sensitive_payload_item(record.stack_info, rules=self._redaction)
            )
        extra_values = {
            field: getattr(record, field)
            for field in set(record.__dict__) - _STANDARD_LOG_RECORD_FIELDS
        }
        for field, redacted_value in redact_sensitive_payload(
            extra_values, rules=self._redaction
        ).items():
            setattr(record, field, redacted_value)
        size = len(json.dumps(record.__dict__, default=str).encode())
        if size > _MAX_LOG_RECORD_BYTES:
            identifiers = set(
                sorted(
                    {
                        key
                        for key, value in record.__dict__.items()
                        if key.endswith("_id") and isinstance(value, str) and len(value) <= 256
                    }
                )[:16]
            )
            for field in LOG_CONTEXT_FIELDS:
                value = getattr(record, field, None)
                if isinstance(value, str):
                    setattr(record, field, value[:256])
            extras = (
                set(record.__dict__)
                - _STANDARD_LOG_RECORD_FIELDS
                - set(LOG_CONTEXT_FIELDS)
                - identifiers
            )
            for field in extras:
                delattr(record, field)
            record.msg = "Log record exceeded size limit"
            record.exc_text = record.stack_info = None
            record.service_name = self._service_name
            record.environment = self._environment
            record.original_bytes = size
            increment_counter(
                "opsmesh_log_records_truncated_total",
                labels={"sink": "service"},
                help_text="Service log records exceeding the byte limit.",
            )
        return True


def configure_logging(
    *,
    redaction: RedactionRules,
    level: str,
    format: str,
    queue_capacity: int,
    service_name: str,
    environment: str,
) -> None:
    global _listener
    instrumentor = LoggingInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument(
            inject_trace_context=True,
            set_logging_format=False,
            enable_log_auto_instrumentation=False,
        )
    shutdown_logging()
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    queue_handler = BoundedLogHandler(Queue(maxsize=queue_capacity))
    queue_handler.addFilter(
        RequestContextFilter(
            redaction=redaction,
            service_name=service_name,
            environment=environment,
        )
    )

    if format == "json":
        formatter = json_log_formatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [service=%(service_name)s env=%(environment)s] "
            "[request_id=%(request_id)s trace_id=%(trace_id)s span_id=%(span_id)s "
            "] %(name)s: %(message)s"
        )

    handler.setFormatter(formatter)
    _listener = DrainingQueueListener(queue_handler.queue, handler, respect_handler_level=True)
    _listener.start()
    root_logger.addHandler(queue_handler)


def json_log_formatter() -> logging.Formatter:
    return JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s "
        "%(service_name)s %(environment)s "
        + " ".join(f"%({field})s" for field in LOG_CONTEXT_FIELDS)
    )
