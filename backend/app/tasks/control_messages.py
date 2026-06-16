from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
        sequence = int(
            self._session.scalar(
                select(func.coalesce(func.max(TaskMessage.sequence), 0)).where(
                    TaskMessage.workspace_id == task.workspace_id,
                    TaskMessage.task_id == task.id,
                )
            )
            or 0
        ) + 1
        message = TaskMessage(
            workspace_id=task.workspace_id,
            task_id=task.id,
            message_type=f"task.control.{action}",
            sequence=sequence,
            body=instruction or reason or action,
            payload={
                "action": action,
                "instruction": instruction,
                "reason": reason,
                "actor_user_id": str(actor_user_id),
                "metadata": metadata,
            },
        )
        self._session.add(message)
        self._session.flush()
        return message
