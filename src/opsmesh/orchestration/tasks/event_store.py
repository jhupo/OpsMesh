from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.orchestration.tasks.models import Task, TaskEventRecord
from opsmesh.orchestration.tasks.notifications import TASK_EVENT_NOTIFICATION_CHANNEL
from opsmesh.orchestration.webhooks.delivery import WebhookDeliveryService


class TaskEventStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object] | None = None,
        available_at: datetime | None = None,
        event_id: UUID | None = None,
    ) -> TaskEventRecord:
        task = self._session.scalar(
            select(Task)
            .where(
                Task.workspace_id == workspace_id,
                Task.id == task_id,
            )
            .with_for_update()
        )
        if task is None:
            raise ValueError("Task event destination is unavailable")
        if event_id is not None:
            existing = self._session.scalar(
                select(TaskEventRecord).where(
                    TaskEventRecord.workspace_id == workspace_id,
                    TaskEventRecord.task_id == task_id,
                    TaskEventRecord.event_id == event_id,
                )
            )
            if existing is not None:
                expected_payload = {**(payload or {}), "event_id": str(event_id)}
                if existing.event_type != event_type or existing.payload != expected_payload:
                    raise ValueError("Task event identity was reused for different content")
                return existing
        sequence = (
            self._session.scalar(
                select(func.max(TaskEventRecord.sequence)).where(
                    TaskEventRecord.workspace_id == workspace_id,
                    TaskEventRecord.task_id == task_id,
                )
            )
            or 0
        )
        event = TaskEventRecord(
            workspace_id=workspace_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload or {},
            sequence=sequence + 1,
            event_id=event_id or uuid4(),
        )
        self._session.add(event)
        self._session.flush([event])
        event.payload = {
            **event.payload,
            "event_id": str(event.event_id),
        }
        self._session.flush([event])
        self._session.execute(
            select(func.pg_notify(TASK_EVENT_NOTIFICATION_CHANNEL, f"{workspace_id}:{task_id}"))
        )
        from opsmesh.orchestration.automations.dispatch import stage_automation_task_progress

        stage_automation_task_progress(
            self._session, workspace_id=workspace_id, task_id=task_id, event_id=event.event_id
        )
        if event.payload.get("message_type") in {
            "pm.acceptance_decision",
            "specialist.handoff",
            "task.plan.created",
        }:
            from opsmesh.teams.execution.dispatch import stage_team_task_progress

            stage_team_task_progress(self._session, task=task, event_id=event.id)
        WebhookDeliveryService(self._session).enqueue_event(
            workspace_id=event.workspace_id,
            event_type=event.event_type,
            payload=event.payload,
            event_id=str(event.event_id),
            available_at=available_at,
        )
        return event
