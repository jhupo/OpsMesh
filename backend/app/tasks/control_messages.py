from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage


class TaskControlMessageWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append_control_message(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        action: str,
        instruction: str | None,
        reason: str | None,
        metadata: dict[str, object],
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type=f"task.control.{action}",
            body=instruction or reason or action,
            payload={
                "action": action,
                "instruction": instruction,
                "reason": reason,
                "actor_user_id": str(actor_user_id),
                "metadata": metadata,
            },
        )
