from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.observability.audit_service import AuditService
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage


class TaskFeedbackService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        body: str,
        feedback_kind: str,
        metadata: dict[str, object],
    ) -> TaskMessage | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None
        message = TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type="human.feedback",
            body=body,
            payload={
                "actor_user_id": str(actor_user_id),
                "feedback_kind": feedback_kind,
                "metadata": metadata,
            },
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.feedback.recorded",
            target_type="task",
            target_id=task_id,
            metadata={
                "message_id": str(message.id),
                "feedback_kind": feedback_kind,
            },
        )
        self._session.commit()
        self._session.refresh(message)
        return message
