import logging
import sys
from traceback import format_exception

from pythonjsonlogger.json import JsonFormatter

from backend.app.core.config import Settings
from backend.app.core.request_context import LOG_CONTEXT_FIELDS, current_log_context
from backend.app.security.redaction import redact_sensitive_payload_item

_STANDARD_LOG_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)


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
            if redacted_exception != exception_text:
                record.exc_info = None
                record.exc_text = str(redacted_exception)
        if record.stack_info is not None:
            record.stack_info = str(redact_sensitive_payload_item(record.stack_info))
        for field in set(record.__dict__) - _STANDARD_LOG_RECORD_FIELDS:
            setattr(record, field, redact_sensitive_payload_item(getattr(record, field)))
        return True


def configure_logging(settings: Settings) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(
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
            "worker_id=%(worker_id)s] %(name)s: %(message)s"
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


def json_log_formatter() -> logging.Formatter:
    return JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s "
        "%(service_name)s %(environment)s "
        + " ".join(f"%({field})s" for field in LOG_CONTEXT_FIELDS)
    )
