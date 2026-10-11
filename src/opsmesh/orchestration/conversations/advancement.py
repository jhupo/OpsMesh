"""Advance durable turns. This module never invokes a model or executes agent code."""

import logging
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.conversations.models import (
    Conversation,
    ConversationExecution,
    ConversationTurn,
    turn_event,
)
from opsmesh.orchestration.conversations.service import TERMINAL, create_execution, task_reply
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.shared.errors import DomainError

logger = logging.getLogger(__name__)


class ConversationAdvanceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def advance(self, workspace_id: UUID, conversation_id: UUID) -> None:
        started = perf_counter()
        conversation = self.session.scalar(
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id, Conversation.id == conversation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        logger.info(
            "Conversation lock acquired",
            extra={
                "workspace_id": str(workspace_id),
                "conversation_id": str(conversation_id),
                "phase": "conversation.lock",
                "duration_ms": round((perf_counter() - started) * 1000),
            },
        )
        if conversation is None:
            return
        while True:
            turn = self.session.scalar(
                select(ConversationTurn)
                .where(
                    ConversationTurn.workspace_id == workspace_id,
                    ConversationTurn.conversation_id == conversation_id,
                    ConversationTurn.status.not_in(TERMINAL),
                )
                .order_by(ConversationTurn.sequence)
                .with_for_update()
                .limit(1)
                .execution_options(populate_existing=True)
            )
            if turn is None:
                return
            self._process(conversation, turn)
            self.session.flush()
            if turn.status not in TERMINAL:
                return

    def _process(self, conversation: Conversation, turn: ConversationTurn) -> None:
        previous = turn.status
        try:
            with self.session.begin_nested():
                started = perf_counter()
                ExecutionIdentityService(self.session).restore(
                    turn.workspace_id, turn.execution_identity
                )
                logger.info(
                    "Conversation identity restored",
                    extra={
                        "conversation_id": str(conversation.id),
                        "turn_id": str(turn.id),
                        "phase": "conversation.identity",
                        "duration_ms": round((perf_counter() - started) * 1000),
                    },
                )
                self._advance(conversation, turn)
        except (DomainError, ValueError, PermissionError):
            turn.status = "failed"
            turn.error_code = "conversation_admission_denied"
        turn.updated_at = datetime.now(UTC)
        if previous != turn.status:
            self.session.add(turn_event(turn))
            AuditService(self.session).record_system_action(
                workspace_id=turn.workspace_id,
                action="conversation.turn.transitioned",
                target_type="conversation_turn",
                target_id=turn.id,
                metadata={
                    "previous_status": previous,
                    "status": turn.status,
                    "error_code": turn.error_code,
                },
            )

    def _advance(self, conversation: Conversation, turn: ConversationTurn) -> None:
        rows = self.session.execute(
            select(ConversationExecution, Task)
            .join(
                Task,
                Task.id == ConversationExecution.task_id,
            )
            .where(
                ConversationExecution.workspace_id == turn.workspace_id,
                ConversationExecution.turn_id == turn.id,
                Task.workspace_id == turn.workspace_id,
            )
            .order_by(ConversationExecution.created_at, ConversationExecution.id)
        ).all()
        if not rows:
            started = perf_counter()
            create_execution(self.session, conversation, turn)
            turn.status = "running"
            logger.info(
                "Conversation execution admitted",
                extra={
                    "workspace_id": str(turn.workspace_id),
                    "conversation_id": str(conversation.id),
                    "turn_id": str(turn.id),
                    "phase": "conversation.admission",
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "accepted_to_admitted_ms": round(
                        (datetime.now(UTC) - turn.created_at.replace(tzinfo=UTC)).total_seconds()
                        * 1000
                    ),
                },
            )
            return
        if len(rows) != 1:
            raise ValueError("Conversation turn must have exactly one execution")
        _, task = rows[0]
        if task.status not in TERMINAL:
            turn.status = "waiting_approval" if task.status == "waiting_approval" else "running"
            return
        turn.status = task.status
        if task.status == "completed":
            turn.reply = task_reply(task)
            output = task.final_output or {}
            final_agent = output.get("final_agent")
            turn.final_agent = final_agent if isinstance(final_agent, dict) else None
            return
        run = self.session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == turn.workspace_id,
                AgentRun.task_id == task.id,
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        error = run.error if run is not None and run.error is not None else {}
        code = error.get("code")
        turn.error_code = str(code)[:120] if code else "conversation_execution_failed"
