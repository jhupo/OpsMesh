from __future__ import annotations

from backend.app.tasks.models import Task


def task_control_state(task: Task) -> dict[str, object]:
    state = task.generic_state if isinstance(task.generic_state, dict) else {}
    control = state.get("control")
    return dict(control) if isinstance(control, dict) else {}


def with_task_control_state(
    generic_state: dict[str, object],
    control: dict[str, object],
) -> dict[str, object]:
    state = dict(generic_state or {})
    state["control"] = control
    return state
