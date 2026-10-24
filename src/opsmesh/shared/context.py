from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class LogContext:
    request_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    user_id: str | None = None
    plugin_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


logging_context: ContextVar[LogContext | None] = ContextVar("logging_context", default=None)


@contextmanager
def bind_logging_context(**fields: str) -> Iterator[None]:
    token = logging_context.set(replace(logging_context.get() or LogContext(), **fields))
    try:
        yield
    finally:
        logging_context.reset(token)
