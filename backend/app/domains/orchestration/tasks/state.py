from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from backend.app.domains.orchestration.tasks.models import Task


class TaskStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}

ALLOWED_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.PLANNING, TaskStatus.QUEUED, TaskStatus.CANCELLED},
    TaskStatus.QUEUED: {
        TaskStatus.PLANNING,
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PLANNING: {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.BLOCKED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_APPROVAL: {
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.BLOCKED: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.QUEUED},
    TaskStatus.CANCELLED: set(),
}


def can_transition_task(current: TaskStatus, next_status: TaskStatus) -> bool:
    return next_status in ALLOWED_TASK_TRANSITIONS[current]


def require_task_transition(current: TaskStatus, next_status: TaskStatus) -> None:
    if not can_transition_task(current, next_status):
        raise ValueError(f"Invalid task transition: {current.value} -> {next_status.value}")


@dataclass(frozen=True)
class TaskTransition:
    previous_status: TaskStatus
    next_status: TaskStatus
    changed: bool


class TaskStateService:
    def reset_to_draft(self, task: Task) -> TaskTransition:
        current_status = TaskStatus(task.status)
        if current_status == TaskStatus.DRAFT:
            return TaskTransition(current_status, TaskStatus.DRAFT, changed=False)
        task.status = TaskStatus.DRAFT.value
        task.completed_at = None
        task.final_output = None
        return TaskTransition(current_status, TaskStatus.DRAFT, changed=True)

    def transition(
        self,
        task: Task,
        next_status: TaskStatus,
        *,
        completed_at: datetime | None = None,
        final_output: dict[str, object] | None = None,
    ) -> TaskTransition:
        current_status = TaskStatus(task.status)
        if current_status == next_status:
            return TaskTransition(current_status, next_status, changed=False)

        require_task_transition(current_status, next_status)
        task.status = next_status.value

        if next_status == TaskStatus.QUEUED:
            task.completed_at = None
            task.final_output = None
        elif next_status in TERMINAL_TASK_STATUSES:
            task.completed_at = completed_at or datetime.now(UTC)

        if final_output is not None:
            task.final_output = final_output

        return TaskTransition(current_status, next_status, changed=True)
