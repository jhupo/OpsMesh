from __future__ import annotations

from backend.app.tasks.models import TaskStep

SCHEDULING_STATUS_KEY = "scheduling_status"
SCHEDULING_STATUS_BLOCKED = "blocked"
BLOCKED_REASON_KEY = "blocked_reason"
BLOCKED_DETAILS_KEY = "blocked_details"
BLOCKED_RESOURCE_KEYS_KEY = "blocked_resource_keys"
SCHEDULED_AT_KEY = "scheduled_at"
PRIORITY_SCORE_KEY = "priority_score"


def mark_step_scheduling_runnable(
    step: TaskStep,
    *,
    scheduled_at: str | None = None,
    priority_score: int | None = None,
) -> None:
    dependencies = _dependencies(step)
    dependencies.pop(SCHEDULING_STATUS_KEY, None)
    dependencies.pop(BLOCKED_REASON_KEY, None)
    dependencies.pop(BLOCKED_DETAILS_KEY, None)
    dependencies.pop(BLOCKED_RESOURCE_KEYS_KEY, None)
    if scheduled_at is not None:
        dependencies[SCHEDULED_AT_KEY] = scheduled_at
    if priority_score is not None:
        dependencies[PRIORITY_SCORE_KEY] = priority_score
    step.dependencies = dependencies


def mark_step_scheduling_blocked(
    step: TaskStep,
    reason: str,
    details: dict[str, object] | None = None,
    *,
    priority_score: int | None = None,
) -> None:
    dependencies = _dependencies(step)
    dependencies[SCHEDULING_STATUS_KEY] = SCHEDULING_STATUS_BLOCKED
    dependencies[BLOCKED_REASON_KEY] = reason
    if details is not None:
        dependencies[BLOCKED_DETAILS_KEY] = details
    dependencies.pop(SCHEDULED_AT_KEY, None)
    if priority_score is not None:
        dependencies[PRIORITY_SCORE_KEY] = priority_score
    step.dependencies = dependencies


def set_blocked_resource_keys(step: TaskStep, keys: list[str]) -> None:
    dependencies = _dependencies(step)
    dependencies[BLOCKED_RESOURCE_KEYS_KEY] = keys
    step.dependencies = dependencies


def _dependencies(step: TaskStep) -> dict[str, object]:
    return dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
