"""Structured process logs with explicit contextual fields."""

import json
import logging
from datetime import UTC, datetime


class StructuredLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("run_id", "request_id", "tool_call_id", "event_id"):
            if hasattr(record, name):
                entry[name] = str(getattr(record, name))
        # Exception details can contain provider data: log the class, not raw payloads.
        if record.exc_info and record.exc_info[0]:
            entry["exception_type"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredLogFormatter())
    logger = logging.getLogger("opsmesh")
    logger.handlers[:] = [handler]
    logger.setLevel(level)
    logger.propagate = False
