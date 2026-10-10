"""Wake a conversation when a committed input or linked task changes."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import event, insert, inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, Session

from opsmesh.orchestration.conversations.models import (
    ConversationExecution,
    ConversationTurn,
)
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.queues.dispatch import stage_job
from opsmesh.runtime.queues.models import QueueDispatch


def conversation_job(
    workspace_id: UUID, conversation_id: UUID, user_id: UUID | None = None
) -> JobPayload:
    request_id = uuid4()
    return JobPayload(
        job_id=request_id,
        workspace_id=workspace_id,
        job_type=JobType.CONVERSATION_ADVANCE,
        resource_id=conversation_id,
        requested_by_user_id=user_id,
        idempotency_key=f"conversation.advance:{conversation_id}:{request_id}",
    )


def request_advance(
    session: Session, workspace_id: UUID, conversation_id: UUID, user_id: UUID | None = None
) -> None:
    stage_job(session, conversation_job(workspace_id, conversation_id, user_id))


def _task_changed(mapper: Mapper[Task], connection: Connection, task: Task) -> None:
    if not inspect(task).attrs.status.history.has_changes():
        return
    conversation_ids = connection.scalars(
        select(ConversationTurn.conversation_id)
        .join(ConversationExecution, ConversationExecution.turn_id == ConversationTurn.id)
        .where(
            ConversationExecution.workspace_id == task.workspace_id,
            ConversationExecution.task_id == task.id,
            ConversationTurn.workspace_id == task.workspace_id,
            ConversationTurn.status.not_in(("completed", "failed", "cancelled")),
        )
        .distinct()
    )
    for conversation_id in conversation_ids:
        job = conversation_job(task.workspace_id, conversation_id).with_trace_context()
        now = datetime.now(UTC)
        connection.execute(
            insert(QueueDispatch).values(
                id=uuid4(),
                workspace_id=task.workspace_id,
                payload=job.model_dump(mode="json"),
                available_at=now,
                created_at=now,
                updated_at=now,
                attempts=0,
            )
        )


def register_task_notifications() -> None:
    if not event.contains(Task, "after_update", _task_changed):
        event.listen(Task, "after_update", _task_changed)
