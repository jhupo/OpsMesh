"""Scoped message routing; task execution and controls remain owned by orchestration."""

import json
from dataclasses import dataclass, field
from uuid import UUID

from opsmesh_plugin_sdk.contracts import IncomingMessage, PendingAction
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.domains.integrations.automation_models import Automation, AutomationEvent
from backend.app.domains.orchestration.approvals.models import Approval
from backend.app.domains.orchestration.tasks.contracts import TaskControlActionRequest
from backend.app.domains.orchestration.tasks.control.service import TaskControlService
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.orchestration.tasks.state import TERMINAL_TASK_STATUSES


@dataclass(frozen=True)
class MessageDispatch:
    handled: bool = False
    previous_context: dict[str, object] = field(default_factory=dict)


def bounded_output(output: dict[str, object]) -> dict[str, object]:
    safe = redact_sensitive_payload(output)
    encoded = json.dumps(safe, ensure_ascii=True)
    if len(encoded) <= 32_000:
        return safe
    return {"preview": encoded[:32_000], "truncated": True}


class AutomationConversationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def require_event(
        self, workspace_id: UUID, automation_id: UUID, event_id: UUID
    ) -> AutomationEvent:
        event = self.session.scalar(
            select(AutomationEvent).where(
                AutomationEvent.workspace_id == workspace_id,
                AutomationEvent.automation_id == automation_id,
                AutomationEvent.id == event_id,
            )
        )
        if event is None:
            raise ValueError("Automation event not found")
        return event

    def target(self, item: Automation, message: IncomingMessage) -> AutomationEvent | None:
        if message.reply_to_event_id is None:
            return None
        target = self.require_event(item.workspace_id, item.id, message.reply_to_event_id)
        if (
            target.conversation_id != message.conversation_id
            or target.input_payload.get("sender_id") != message.sender_id
        ):
            raise ValueError("Message target does not belong to this sender and conversation")
        return target

    def task(self, event: AutomationEvent, *, lock: bool = False) -> Task | None:
        query = select(Task).where(
            Task.workspace_id == event.workspace_id, Task.id == event.task_id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def pending_actions(self, event: AutomationEvent) -> list[PendingAction]:
        if event.task_id is None:
            return []
        return [
            PendingAction(id=row.id, kind=row.approval_type, risk_level=row.risk_level)
            for row in self.session.scalars(
                select(Approval)
                .where(
                    Approval.workspace_id == event.workspace_id,
                    Approval.task_id == event.task_id,
                    Approval.status == "pending",
                )
                .order_by(Approval.created_at, Approval.id)
                .limit(100)
            )
        ]

    def dispatch(
        self, item: Automation, event: AutomationEvent, message: IncomingMessage
    ) -> MessageDispatch:
        target = self.target(item, message)
        if target is None:
            return MessageDispatch()
        task = self.task(target, lock=True)
        if task is None:
            if target.status == "pending":
                return MessageDispatch(handled=True)
            raise ValueError("Target message has no task")
        terminal = task.status in {status.value for status in TERMINAL_TASK_STATUSES}
        if terminal and message.action == "follow_up":
            return MessageDispatch(
                previous_context={
                    "event_id": str(target.id),
                    "task_id": str(task.id),
                    "status": task.status,
                    "output": bounded_output(task.final_output or {}),
                }
            )
        if terminal:
            raise ValueError("Terminal tasks require a follow_up message to start new work")
        action = "add_instruction" if message.action == "follow_up" else message.action
        result = TaskControlService(self.session).apply_action(
            workspace_id=item.workspace_id,
            task_id=task.id,
            actor_user_id=item.created_by_user_id,
            request=TaskControlActionRequest(
                action=action,
                instruction=message.text if action == "add_instruction" else None,
                reason="automation_message",
                enqueue=action == "resume",
                metadata={"automation_event_id": str(event.id), "sender_id": message.sender_id},
            ),
            commit=False,
        )
        if result is None:
            raise ValueError("Message target task disappeared")
        event.task_id = task.id
        event.result_payload = {"action": action, "task_status": task.status}
        event.status = "control_applied"
        return MessageDispatch(handled=True)
