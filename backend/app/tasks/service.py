from dataclasses import dataclass
from datetime import UTC, datetime

from backend.app.tasks.models import Task
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus, require_task_transition


@dataclass(frozen=True)
class TaskTransition:
    previous_status: TaskStatus
    next_status: TaskStatus
    changed: bool


class TaskStateService:
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
