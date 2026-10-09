"""Advance durable turns. This module never invokes a model or executes agent code."""

import json
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from backend.app.governance.audit.service import AuditService
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.orchestration.conversations.models import (
    Conversation,
    ConversationExecution,
    ConversationTurn,
    turn_event,
)
from backend.app.orchestration.conversations.service import TERMINAL, create_execution, task_reply
from backend.app.orchestration.tasks.models import Task
from backend.app.shared.errors import DomainError


class ConversationMaintenanceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def process_one(self) -> bool:
        older = aliased(ConversationTurn)
        turn = self.session.scalar(
            select(ConversationTurn)
            .join(Conversation, Conversation.id == ConversationTurn.conversation_id)
            .where(
                ConversationTurn.status.not_in(TERMINAL),
                ~exists(
                    select(older.id).where(
                        older.workspace_id == ConversationTurn.workspace_id,
                        older.conversation_id == ConversationTurn.conversation_id,
                        older.sequence < ConversationTurn.sequence,
                        older.status.not_in(TERMINAL),
                    )
                ),
            )
            .order_by(ConversationTurn.updated_at, ConversationTurn.id)
            .with_for_update(of=Conversation, skip_locked=True)
            .limit(1)
        )
        if turn is None:
            return False
        turn = self.session.scalar(
            select(ConversationTurn)
            .where(
                ConversationTurn.workspace_id == turn.workspace_id,
                ConversationTurn.id == turn.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if turn is None or turn.status in TERMINAL:
            return True
        conversation = self.session.scalar(
            select(Conversation).where(
                Conversation.workspace_id == turn.workspace_id,
                Conversation.id == turn.conversation_id,
            )
        )
        if conversation is None:
            return False
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
        # Rotate waiting turns so one long task cannot starve other conversations.
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
        return True

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
            turn.error_code = "conversation_execution_unsuccessful"
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
        history = list(
            self.session.scalars(
                select(ConversationTurn)
                .where(
                    ConversationTurn.workspace_id == turn.workspace_id,
                    ConversationTurn.conversation_id == conversation.id,
                    ConversationTurn.sequence < turn.sequence,
                    ConversationTurn.status.in_(TERMINAL),
                )
                .order_by(ConversationTurn.sequence.desc())
                .limit(12)
            )
        )
        context = [
            {
                "user": item.body[:8000],
                "assistant": (item.reply or "")[:8000],
                "status": item.status,
                "error_code": item.error_code,
            }
            for item in reversed(history)
        ]
        body = json.dumps(
            {"history": context, "user_message": turn.body, "delegated_results": results},
            ensure_ascii=False,
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
