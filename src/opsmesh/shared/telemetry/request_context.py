from __future__ import annotations

from contextvars import ContextVar, Token

from opsmesh.shared.telemetry.trace_context import current_trace_metadata

LOG_CONTEXT_FIELDS = (
    "request_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "workspace_id",
    "user_id",
    "task_id",
    "run_id",
    "execution_id",
    "workflow_id",
    "workflow_run_id",
    "activity_id",
    "activity_attempt",
    "worker_id",
    "runtime_id",
    "conversation_id",
    "tool_call_id",
    "operation_id",
    "error_id",
)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
worker_id_var: ContextVar[str | None] = ContextVar("worker_id", default=None)
runtime_id_var: ContextVar[str | None] = ContextVar("runtime_id", default=None)

_CONTEXT_VARS = {
    "request_id": request_id_var,
    "workspace_id": workspace_id_var,
    "user_id": user_id_var,
    "task_id": task_id_var,
    "run_id": run_id_var,
    "worker_id": worker_id_var,
    "runtime_id": runtime_id_var,
}
_CONTEXT_VARS.update(
    {
        field: ContextVar(field, default=None)
        for field in (
            "execution_id",
            "workflow_id",
            "workflow_run_id",
            "activity_id",
            "activity_attempt",
            "conversation_id",
            "tool_call_id",
            "operation_id",
            "error_id",
        )
    }
)


def current_log_context() -> dict[str, str | None]:
    context = {field: variable.get() for field, variable in _CONTEXT_VARS.items()}
    context.update(trace_id=None, span_id=None, parent_span_id=None)
    context.update(current_trace_metadata())
    return context


def current_evidence_context() -> dict[str, str]:
    return {field: value for field, value in current_log_context().items() if value is not None}


def set_log_context(**values: object) -> dict[str, Token[str | None]]:
    tokens: dict[str, Token[str | None]] = {}
    for field, value in values.items():
        context_var = _CONTEXT_VARS.get(field)
        if context_var is None:
            continue
        tokens[field] = context_var.set(str(value) if value is not None else None)
    return tokens


def reset_log_context(tokens: dict[str, Token[str | None]]) -> None:
    for field, token in reversed(tokens.items()):
        _CONTEXT_VARS[field].reset(token)
