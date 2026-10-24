import json
import logging
import os
import traceback
from dataclasses import asdict
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .context import LogContext, logging_context


class Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        item = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **{
                key: value
                for key, value in asdict(logging_context.get() or LogContext()).items()
                if value is not None
            },
        }
        for name in ("trace_id", "span_id", "request_id", "method", "route", "status"):
            if hasattr(record, name):
                item[name] = getattr(record, name)
        if record.exc_info:
            item["exception_type"] = (
                record.exc_info[0].__name__ if record.exc_info[0] else "unknown"
            )
            # Stack locations are useful for diagnosis without disclosing exception payloads.
            item["exception_stack"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(item, ensure_ascii=True)


def configure(
    directory: Path,
    level: str,
    *,
    console: bool = True,
    file: bool = True,
    max_bytes: int = 10_000_000,
    backup_count: int = 5,
) -> None:
    handlers: list[logging.Handler] = []
    if console:
        handlers.append(logging.StreamHandler())
    if file:
        directory.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                directory / f"opsmesh-{os.getpid()}.log",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        )
    if not handlers:
        handlers.append(logging.NullHandler())
    for handler in handlers:
        handler.setFormatter(Formatter())
    logging.basicConfig(level=level, handlers=handlers, force=True)
    for name in ("httpx", "httpx2", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
