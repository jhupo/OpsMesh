"""Persist release of native execution resources before another execution proceeds."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.automations.dispatch import stage_automation_task_progress
from opsmesh.orchestration.conversations.dispatch import request_advance
from opsmesh.orchestration.conversations.models import ConversationExecution, ConversationTurn
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.statuses import TERMINAL_RUN_STATUS_VALUES


def task_has_unreleased_execution(session: Session, workspace_id: UUID, task_id: UUID) -> bool:
    return (
        session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == task_id,
                AgentRun.started_at.is_not(None),
                AgentRun.execution_released_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def release_execution(session: Session, workspace_id: UUID, run_id: UUID) -> None:
    run = session.scalar(
        select(AgentRun)
        .where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.id == run_id,
        )
        .with_for_update()
    )
    if run is None:
        raise ValueError("Execution release does not belong to the workspace")
    if run.execution_released_at is not None:
        return
    if run.status not in TERMINAL_RUN_STATUS_VALUES:
        raise ValueError("A live execution cannot release its session")
    run.execution_released_at = datetime.now(UTC)
    conversations = session.scalars(
        select(ConversationTurn.conversation_id)
        .join(ConversationExecution, ConversationExecution.turn_id == ConversationTurn.id)
        .where(
            ConversationTurn.workspace_id == workspace_id,
            ConversationExecution.workspace_id == workspace_id,
            ConversationExecution.task_id == run.task_id,
        )
        .distinct()
    ).all()
    for conversation_id in conversations:
        request_advance(session, workspace_id, conversation_id)
    if run.task_id is not None:
        stage_automation_task_progress(
            session,
            workspace_id=workspace_id,
            task_id=run.task_id,
            event_id=uuid5(run.id, "execution.released"),
        )
