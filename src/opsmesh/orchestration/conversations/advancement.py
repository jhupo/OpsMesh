"""Advance durable turns. This module never invokes a model or executes agent code."""

import json
from datetime import UTC, datetime
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


class ConversationAdvanceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def advance(self, workspace_id: UUID, conversation_id: UUID) -> None:
        conversation = self.session.scalar(
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id, Conversation.id == conversation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
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
        previous = (turn.status, turn.round)
        try:
            with self.session.begin_nested():
                ExecutionIdentityService(self.session).restore(
                    turn.workspace_id, turn.execution_identity
                )
                self._advance(conversation, turn)
        except (DomainError, ValueError, PermissionError):
            turn.status = "failed"
            turn.error_code = "conversation_admission_denied"
        turn.updated_at = datetime.now(UTC)
        if previous != (turn.status, turn.round):
            self.session.add(turn_event(turn))
            AuditService(self.session).record_system_action(
                workspace_id=turn.workspace_id,
                action="conversation.turn.transitioned",
                target_type="conversation_turn",
                target_id=turn.id,
                metadata={
                    "previous_status": previous[0],
                    "status": turn.status,
                    "round": turn.round,
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
            self._start(conversation, turn, [])
            return
        current = [(link, task) for link, task in rows if link.round == turn.round]
        managers = [task for link, task in current if link.purpose == "manager"]
        if not managers:
            raise ValueError("Conversation manager execution is missing")
        manager = managers[-1]
        if manager.status not in TERMINAL:
            turn.status = "waiting_approval" if manager.status == "waiting_approval" else "running"
            return
        children = [task for link, task in current if link.purpose == "delegate"]
        if any(task.status not in TERMINAL for task in children):
            turn.status = "waiting_tasks"
            return
        if manager.status != "completed":
            turn.status = manager.status
            run = self.session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == turn.workspace_id,
                    AgentRun.task_id == manager.id,
                )
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .limit(1)
            )
            error = run.error if run is not None and run.error is not None else {}
            code = error.get("code")
            turn.error_code = str(code)[:120] if code else "conversation_execution_failed"
            return
        if children:
            if turn.round >= 3:
                turn.status = "failed"
                turn.error_code = "conversation_delegation_limit"
                return
            turn.round += 1
            self._start(
                conversation,
                turn,
                [
                    {
                        "task_id": str(task.id),
                        "status": task.status,
                        "result": task_reply(task)[:16000],
                    }
                    for link, task in rows
                    if link.purpose == "delegate"
                ],
            )
            return
        turn.reply = task_reply(manager)
        turn.status = "completed"

    def _start(
        self, conversation: Conversation, turn: ConversationTurn, results: list[dict[str, object]]
    ) -> None:
        # Only new input crosses this boundary; the SDK Session owns prior history.
        body = (
            json.dumps({"delegated_results": results}, ensure_ascii=False) if results else turn.body
        )
        create_execution(
            self.session,
            conversation,
            turn,
            key=f"manager:{turn.round}",
            purpose="manager",
            body=body,
            agent_id=conversation.agent_profile_id,
            team_id=conversation.agent_team_id,
        )
        turn.status = "running"
