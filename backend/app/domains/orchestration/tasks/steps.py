from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.app.domains.orchestration.tasks.models import TaskStep


class TaskStepStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


FINAL_STEP_STATUSES = {
    TaskStepStatus.COMPLETED,
    TaskStepStatus.FAILED,
    TaskStepStatus.CANCELLED,
    TaskStepStatus.SKIPPED,
}


ALLOWED_STEP_TRANSITIONS: dict[TaskStepStatus, set[TaskStepStatus]] = {
    TaskStepStatus.QUEUED: {
        TaskStepStatus.SKIPPED,
        TaskStepStatus.RUNNING,
        TaskStepStatus.BLOCKED,
        TaskStepStatus.CANCELLED,
    },
    TaskStepStatus.RUNNING: {
        TaskStepStatus.BLOCKED,
        TaskStepStatus.COMPLETED,
        TaskStepStatus.FAILED,
        TaskStepStatus.CANCELLED,
    },
    TaskStepStatus.BLOCKED: {
        TaskStepStatus.QUEUED,
        TaskStepStatus.RUNNING,
        TaskStepStatus.FAILED,
        TaskStepStatus.CANCELLED,
    },
    TaskStepStatus.COMPLETED: set(),
    TaskStepStatus.FAILED: {TaskStepStatus.QUEUED},
    TaskStepStatus.CANCELLED: set(),
    TaskStepStatus.SKIPPED: set(),
}


def can_transition_step(current: TaskStepStatus, next_status: TaskStepStatus) -> bool:
    return next_status in ALLOWED_STEP_TRANSITIONS[current]


def require_step_transition(current: TaskStepStatus, next_status: TaskStepStatus) -> None:
    if not can_transition_step(current, next_status):
        raise ValueError(f"Invalid task step transition: {current.value} -> {next_status.value}")


def step_message_payload(step: TaskStep) -> dict[str, object]:
    return {
        "work_package_id": step.work_package_id,
        "required_role": step.required_role,
        "required_skills": step.required_skills,
        "expected_artifacts": step.expected_artifacts,
        "acceptance_criteria": step.acceptance_criteria,
        "review_policy": step.review_policy,
    }


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
        result_payload: dict[str, object] | None = None,
    ) -> TaskStepTransition:
        current_status = TaskStepStatus(step.status)
        if current_status == next_status:
            if dependencies is not None:
                step.dependencies = dependencies
            if result_summary is not None:
                step.result_summary = result_summary
            if result_payload is not None:
                step.result_payload = result_payload
            return TaskStepTransition(current_status, next_status, changed=False)

        require_step_transition(current_status, next_status)
        step.status = next_status.value
        if dependencies is not None:
            step.dependencies = dependencies
        if result_summary is not None:
            step.result_summary = result_summary
        if result_payload is not None:
            step.result_payload = result_payload
        return TaskStepTransition(current_status, next_status, changed=True)
