"""Wake admitted message workflows in the same transaction as task evidence."""

from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from opsmesh.orchestration.automations.models import AutomationEvent
from opsmesh.runtime.temporal.outbox import stage_signal


def stage_automation_task_progress(
    session: Session, *, workspace_id: UUID, task_id: UUID, event_id: UUID
) -> None:
    events = session.scalars(
        select(AutomationEvent).where(
            AutomationEvent.workspace_id == workspace_id,
            AutomationEvent.task_id == task_id,
            AutomationEvent.status.in_({"dispatched", "control_applied", "reply_pending"}),
        )
    ).all()
    for event in events:
        _wake(session, event, "reply_delivery", event_id)
    predecessor = aliased(AutomationEvent)
    pending = session.scalars(
        select(AutomationEvent).where(
            AutomationEvent.workspace_id == workspace_id,
            AutomationEvent.status == "pending",
            select(predecessor.id)
            .where(
                predecessor.workspace_id == AutomationEvent.workspace_id,
                predecessor.automation_id == AutomationEvent.automation_id,
                predecessor.source_connection_id.is_not_distinct_from(
                    AutomationEvent.source_connection_id
                ),
                predecessor.conversation_id == AutomationEvent.conversation_id,
                predecessor.task_id == task_id,
            )
            .exists(),
        )
    ).all()
    for event in pending:
        _wake(session, event, "entry_event", event_id)


def _wake(session: Session, event: AutomationEvent, kind: str, evidence_id: UUID) -> None:
    stage_signal(
        session,
        dispatch_id=uuid5(evidence_id, f"{kind}:{event.id}"),
        workspace_id=event.workspace_id,
        workflow_id=f"opsmesh:{event.workspace_id}:{kind}:{event.id}",
        signal_name="wake",
        resource_id=event.id,
    )
