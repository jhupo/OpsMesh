from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from backend.app.core.trace_context import parent_span_id_var, span_id_var, trace_id_var

LOG_CONTEXT_FIELDS = (
    "request_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "workspace_id",
    "user_id",
    "task_id",
    "run_id",
    "worker_id",
)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
worker_id_var: ContextVar[str | None] = ContextVar("worker_id", default=None)

_CONTEXT_VARS = {
    "request_id": request_id_var,
    "trace_id": trace_id_var,
    "span_id": span_id_var,
    "parent_span_id": parent_span_id_var,
    "workspace_id": workspace_id_var,
    "user_id": user_id_var,
    "task_id": task_id_var,
    "run_id": run_id_var,
    "worker_id": worker_id_var,
}


def current_log_context() -> dict[str, str | None]:
    return {field: _CONTEXT_VARS[field].get() for field in LOG_CONTEXT_FIELDS}


def set_log_context(**values: object) -> dict[str, Token[str | None]]:
    tokens: dict[str, Token[str | None]] = {}
    for field, value in values.items():
        context_var = _CONTEXT_VARS.get(field)
        if context_var is None or value is None:
            continue
        tokens[field] = context_var.set(str(value))
    return tokens


def reset_log_context(tokens: dict[str, Token[str | None]]) -> None:
    for field, token in reversed(tokens.items()):
        _CONTEXT_VARS[field].reset(token)


@contextmanager
def log_context(**values: object) -> Iterator[None]:
    tokens = set_log_context(**values)
    try:
        yield
    finally:
        reset_log_context(tokens)
