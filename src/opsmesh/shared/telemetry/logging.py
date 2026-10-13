import atexit
import json
import logging
import sys
from logging.handlers import QueueHandler, QueueListener
from queue import Full, Queue
from traceback import format_exception
from typing import cast

from opentelemetry.instrumentation.logging import LoggingInstrumentor
from pythonjsonlogger.json import JsonFormatter

from opsmesh.shared.config import Settings
from opsmesh.shared.security.redaction import redact_sensitive_payload_item
from opsmesh.shared.telemetry.metrics import metrics_registry
from opsmesh.shared.telemetry.request_context import LOG_CONTEXT_FIELDS, current_log_context

_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
_listener: QueueListener | None = None


class BoundedLogHandler(QueueHandler):
    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except Full:
            metrics_registry.increment(
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


atexit.register(shutdown_logging)


class RequestContextFilter(logging.Filter):
    def __init__(
        self,
        *,
        service_name: str | None = None,
        environment: str | None = None,
    ) -> None:
        super().__init__()
        self._service_name = service_name
        self._environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        for field, value in current_log_context().items():
            if value is not None or not hasattr(record, field):
                setattr(record, field, value)
        record.service_name = self._service_name
        record.environment = self._environment
        try:
            rendered_message = record.getMessage()
        except (TypeError, ValueError):
            rendered_message = str(record.msg)
        record.msg = redact_sensitive_payload_item(rendered_message)
        record.args = ()
        if record.exc_info is not None:
            exception_text = "".join(format_exception(*record.exc_info))
            redacted_exception = redact_sensitive_payload_item(exception_text)
            record.exc_info = None
            record.exc_text = str(redacted_exception)
        if record.stack_info is not None:
            record.stack_info = str(redact_sensitive_payload_item(record.stack_info))
        for field in set(record.__dict__) - _STANDARD_LOG_RECORD_FIELDS:
            setattr(record, field, redact_sensitive_payload_item(getattr(record, field)))
        size = len(json.dumps(record.__dict__, default=str).encode())
        if size > 16_384:
            extras = set(record.__dict__) - _STANDARD_LOG_RECORD_FIELDS - set(LOG_CONTEXT_FIELDS)
            for field in extras:
                delattr(record, field)
            record.msg = "Log record exceeded size limit"
            record.exc_text = record.stack_info = None
            record.service_name = self._service_name
            record.environment = self._environment
            record.original_bytes = size
            metrics_registry.increment(
                "opsmesh_log_records_truncated_total",
                labels={"sink": "service"},
                help_text="Service log records exceeding the byte limit.",
            )
        return True


def configure_logging(settings: Settings) -> None:
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
    root_logger.setLevel(settings.log_level.upper())
    # Observe native SDK compaction evidence without enabling compaction or setting its policy.
    # HTTP bodies and other SDK internals stay at the configured level.
    logging.getLogger("openai-agents.openai.compaction").setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    queue_handler = BoundedLogHandler(Queue(maxsize=settings.log_queue_capacity))
    queue_handler.addFilter(
        RequestContextFilter(
            service_name=settings.service_name,
            environment=settings.environment,
        )
    )

    if settings.log_format == "json":
        formatter = json_log_formatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [service=%(service_name)s env=%(environment)s] "
            "[request_id=%(request_id)s trace_id=%(trace_id)s span_id=%(span_id)s "
            "parent_span_id=%(parent_span_id)s workspace_id=%(workspace_id)s "
            "user_id=%(user_id)s task_id=%(task_id)s run_id=%(run_id)s "
            "worker_id=%(worker_id)s runtime_id=%(runtime_id)s] %(name)s: %(message)s"
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
