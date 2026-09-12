from enum import StrEnum


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
