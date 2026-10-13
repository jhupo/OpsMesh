"""Wake a conversation when a committed input or linked task changes."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import event, insert, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, Session

from opsmesh.orchestration.conversations.models import (
    ConversationExecution,
    ConversationTurn,
)
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.temporal.contracts import (
    DispatchOperation,
    DispatchRequest,
    ExecutionKind,
    ExecutionRequest,
    WorkflowSignal,
)
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.runtime.temporal.outbox import stage_execution


def conversation_workflow_id(workspace_id: UUID, conversation_id: UUID) -> str:
    return f"opsmesh:{workspace_id}:conversation:{conversation_id}"


def request_advance(
    session: Session, workspace_id: UUID, conversation_id: UUID, user_id: UUID | None = None
) -> None:
    stage_execution(
        session,
        ExecutionRequest(
            execution_id=conversation_id,
            workspace_id=workspace_id,
            resource_id=conversation_id,
            kind=ExecutionKind.CONVERSATION,
            requested_by_user_id=user_id,
        ),
        dispatch_id=uuid4(),
    )


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
        dispatch_id = uuid4()
        request = DispatchRequest(
            dispatch_id=dispatch_id,
            workspace_id=task.workspace_id,
            workflow_id=conversation_workflow_id(task.workspace_id, conversation_id),
            operation=DispatchOperation.SIGNAL,
            signal_name="wake",
            signal=WorkflowSignal(resource_id=task.id),
        )
        now = datetime.now(UTC)
        connection.execute(
            insert(TemporalOutboxEntry).values(
                id=dispatch_id,
                workspace_id=task.workspace_id,
                workflow_id=request.workflow_id,
                operation=request.operation.value,
                payload=request.model_dump(mode="json"),
                available_at=now,
                created_at=now,
                updated_at=now,
                attempts=0,
            )
        )
        connection.execute(text("SELECT pg_notify('opsmesh_temporal_dispatch', '')"))


def register_task_notifications() -> None:
    if not event.contains(Task, "after_update", _task_changed):
        event.listen(Task, "after_update", _task_changed)
