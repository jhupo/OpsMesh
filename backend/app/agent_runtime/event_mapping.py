from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.app.agent_runtime.contracts import AgentRuntimeEvent
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import TaskStep

_SECRET_KEYS = {
    "api_key",
    "authorization",
    "base_url",
    "credential",
    "credentials",
    "external_ref",
    "headers",
    "key",
    "secret",
    "token",
}


@dataclass(frozen=True)
class TaskMessageDraft:
    message_type: str
    body: str
    payload: dict[str, object]


class RuntimeEventTaskMessageMapper:
    def map_event(
        self,
        *,
        event: AgentRuntimeEvent,
        run: AgentRun,
        step: TaskStep | None,
    ) -> TaskMessageDraft | None:
        message_type = _message_type(event.event_type)
        if message_type is None:
            return None
        payload = _sanitize(
            {
                **event.payload,
                "runtime_event_type": event.event_type,
                "agent_run_id": str(run.id),
                "task_step_id": str(step.id) if step is not None else None,
                "work_package_id": step.work_package_id if step is not None else None,
            }
        )
        return TaskMessageDraft(
            message_type=message_type,
            body=event.message or _default_body(message_type),
            payload=payload,
        )


def _message_type(event_type: str) -> str | None:
    normalized = event_type.strip().lower().replace("_", ".")
    mapping = {
        "agent.handoff": "agent.handoff",
        "handoff": "agent.handoff",
        "tool.called": "tool.requested",
        "tool.call": "tool.requested",
        "tool.requested": "tool.requested",
        "tool.completed": "tool.completed",
        "tool.result": "tool.completed",
        "tool.failed": "tool.failed",
        "approval.requested": "approval.requested",
        "model.provider.fallback.selected": "model.fallback",
        "model_provider.fallback_selected": "model.fallback",
    }
    return mapping.get(normalized)


def _default_body(message_type: str) -> str:
    return {
        "agent.handoff": "Agent handoff.",
        "tool.requested": "Tool requested.",
        "tool.completed": "Tool completed.",
        "tool.failed": "Tool failed.",
        "approval.requested": "Approval requested.",
        "model.fallback": "Model provider fallback selected.",
    }.get(message_type, "Runtime event.")


def _sanitize(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                sanitized[key_text] = "[redacted]"
            else:
                sanitized[key_text] = _sanitize(item)
        return sanitized
    if isinstance(value, list | tuple):
        return [_sanitize(item) for item in value]
    return str(value)


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in _SECRET_KEYS or any(part in normalized for part in _SECRET_KEYS)
