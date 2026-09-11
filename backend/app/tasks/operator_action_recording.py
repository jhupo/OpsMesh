from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.observability.audit_service import AuditService
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage
from backend.app.tasks.operator_action_contracts import TaskOperatorActionResult


class TaskOperatorActionRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append_message(
        self,
        task: Task,
        *,
        action: str,
        result: TaskOperatorActionResult,
        instruction: str | None,
        reason: str | None,
        metadata: dict[str, object],
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type=f"task.operator.{action}",
            body=f"Operator action applied: {action}.",
            payload={
                "action": action,
                "instruction": instruction,
                "reason": reason,
                "changed_step_ids": [str(step_id) for step_id in result["changed_step_ids"]],
                "created_step_ids": [str(step_id) for step_id in result["created_step_ids"]],
                "warnings": result["warnings"],
                "metadata": metadata,
            },
        )

    def record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        task: Task,
        action: str,
        task_step_ids: list[UUID],
        agent_profile_id: UUID | None,
        result: TaskOperatorActionResult,
        message: TaskMessage,
        reason: str | None,
        metadata: dict[str, object],
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action=f"task.operator.{action}",
            target_type="task",
            target_id=task.id,
            metadata={
                "task_step_ids": [str(step_id) for step_id in task_step_ids],
                "agent_profile_id": str(agent_profile_id) if agent_profile_id else None,
                "changed_step_ids": [str(step_id) for step_id in result["changed_step_ids"]],
                "created_step_ids": [str(step_id) for step_id in result["created_step_ids"]],
                "message_id": str(message.id),
                "reason": reason,
                "metadata": metadata,
            },
        )
