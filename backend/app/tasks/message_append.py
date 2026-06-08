from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.tasks.event_outbox import TaskEventOutboxService
from backend.app.tasks.models import Task, TaskMessage

TASK_MESSAGE_CREATED_EVENT_TYPE = "task.message.created"


@dataclass(frozen=True)
class TaskMessageAppendRequest:
    workspace_id: UUID
    task_id: UUID
    message_type: str
    body: str
    payload: dict[str, object] | None = None
    task_step_id: UUID | None = None
    agent_run_id: UUID | None = None
    agent_profile_id: UUID | None = None


class TaskMessageAppendService:
    """Append task messages with per-task sequence allocation guarded by the task row."""

    def __init__(
        self,
        session: Session,
        *,
        max_retries: int = 3,
    ) -> None:
        self._session = session
        self._event_outbox = TaskEventOutboxService(session)
        self._max_retries = max(1, max_retries)

    def append(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        message_type: str,
        body: str,
        payload: dict[str, object] | None = None,
        task_step_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        agent_profile_id: UUID | None = None,
    ) -> TaskMessage:
        return self.append_request(
            TaskMessageAppendRequest(
                workspace_id=workspace_id,
                task_id=task_id,
                task_step_id=task_step_id,
                agent_run_id=agent_run_id,
                agent_profile_id=agent_profile_id,
                message_type=message_type,
                body=body,
                payload=payload,
            )
        )

    def append_for_task(
        self,
        task: Task,
        *,
        message_type: str,
        body: str,
        payload: dict[str, object] | None = None,
        task_step_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        agent_profile_id: UUID | None = None,
    ) -> TaskMessage:
        return self.append(
            workspace_id=task.workspace_id,
            task_id=task.id,
            task_step_id=task_step_id,
            agent_run_id=agent_run_id,
            agent_profile_id=agent_profile_id,
            message_type=message_type,
            body=body,
            payload=payload,
        )

    def append_request(self, request: TaskMessageAppendRequest) -> TaskMessage:
        last_error: IntegrityError | None = None
        for _ in range(self._max_retries):
            self._lock_task(request.workspace_id, request.task_id)
            sequence = self._next_sequence(request.workspace_id, request.task_id)
            try:
                with self._session.begin_nested():
                    message = self._build_message(request, sequence)
                    self._session.add(message)
                    self._session.flush([message])
                self._enqueue_message_created(message)
                return message
            except IntegrityError as exc:
                if not _is_sequence_collision(exc):
                    raise
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("Task message append failed without an integrity error")

    def _lock_task(self, workspace_id: UUID, task_id: UUID) -> None:
        locked_task_id = self._session.scalar(
            select(Task.id)
            .where(Task.workspace_id == workspace_id, Task.id == task_id)
            .with_for_update()
        )
        if locked_task_id is None:
            raise ValueError("Task not found")

    def _next_sequence(self, workspace_id: UUID, task_id: UUID) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskMessage.sequence), 0)).where(
                TaskMessage.workspace_id == workspace_id,
                TaskMessage.task_id == task_id,
            )
        )
        return int(current or 0) + 1

    def _build_message(
        self,
        request: TaskMessageAppendRequest,
        sequence: int,
    ) -> TaskMessage:
        return TaskMessage(
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            task_step_id=request.task_step_id,
            agent_run_id=request.agent_run_id,
            agent_profile_id=request.agent_profile_id,
            message_type=request.message_type,
            sequence=sequence,
            body=request.body,
            payload=request.payload or {},
        )

    def _enqueue_message_created(self, message: TaskMessage) -> None:
        self._event_outbox.enqueue(
            workspace_id=message.workspace_id,
            task_id=message.task_id,
            event_type=TASK_MESSAGE_CREATED_EVENT_TYPE,
            payload=_message_created_payload(message),
        )


def _is_sequence_collision(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    if constraint_name == "uq_task_messages_task_sequence":
        return True
    text = str(orig or exc)
    return (
        "uq_task_messages_task_sequence" in text
        or "task_messages.task_id, task_messages.sequence" in text
        or "task_messages_task_id_sequence" in text
    )


def _message_created_payload(message: TaskMessage) -> dict[str, object]:
    created_at = message.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return {
        "message_id": str(message.id),
        "message_type": message.message_type,
        "sequence": message.sequence,
        "task_step_id": str(message.task_step_id) if message.task_step_id is not None else None,
        "agent_run_id": str(message.agent_run_id) if message.agent_run_id is not None else None,
        "agent_profile_id": str(message.agent_profile_id)
        if message.agent_profile_id is not None
        else None,
        "has_body": bool(message.body),
        "payload_keys": sorted(str(key) for key in message.payload),
        "created_at": created_at.isoformat(),
    }
