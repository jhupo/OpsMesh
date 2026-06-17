from __future__ import annotations

from dataclasses import dataclass

from backend.app.tasks.models import TaskStep
from backend.app.tasks.step_status import TaskStepStatus, require_step_transition


@dataclass(frozen=True)
class TaskStepTransition:
    previous_status: TaskStepStatus
    next_status: TaskStepStatus
    changed: bool


class TaskStepStateService:
    def transition(
        self,
        step: TaskStep,
        next_status: TaskStepStatus,
        *,
        dependencies: dict[str, object] | None = None,
        result_summary: str | None = None,
    ) -> TaskStepTransition:
        current_status = TaskStepStatus(step.status)
        if current_status == next_status:
            if dependencies is not None:
                step.dependencies = dependencies
            if result_summary is not None:
                step.result_summary = result_summary
            return TaskStepTransition(current_status, next_status, changed=False)

        require_step_transition(current_status, next_status)
        step.status = next_status.value
        if dependencies is not None:
            step.dependencies = dependencies
        if result_summary is not None:
            step.result_summary = result_summary
        return TaskStepTransition(current_status, next_status, changed=True)
