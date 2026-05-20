import logging
import sys

from pythonjsonlogger.json import JsonFormatter

from backend.app.core.config import Settings
from backend.app.core.request_context import LOG_CONTEXT_FIELDS, current_log_context


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for field, value in current_log_context().items():
            setattr(record, field, value)
        return True


def configure_logging(settings: Settings) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestContextFilter())

    if settings.log_format == "json":
        formatter: logging.Formatter = JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s "
            + " ".join(f"%({field})s" for field in LOG_CONTEXT_FIELDS)
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s "
            "[request_id=%(request_id)s workspace_id=%(workspace_id)s "
            "user_id=%(user_id)s task_id=%(task_id)s run_id=%(run_id)s "
            "worker_id=%(worker_id)s] %(name)s: %(message)s"
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
