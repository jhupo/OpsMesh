from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.tasks.events import TaskEventBus
from backend.app.tasks.models import TaskEventOutbox
from backend.app.webhooks.service import WebhookDeliveryService

TASK_EVENT_OUTBOX_PENDING = "pending"
TASK_EVENT_OUTBOX_PUBLISHED = "published"
TASK_EVENT_OUTBOX_ERROR_MAX_LENGTH = 2_000


@dataclass(frozen=True)
class TaskEventOutboxPublishSummary:
    scanned: int
    published: int
    failed: int


class TaskEventOutboxService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object] | None = None,
        available_at: datetime | None = None,
    ) -> TaskEventOutbox:
        event = TaskEventOutbox(
            workspace_id=workspace_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload or {},
            status=TASK_EVENT_OUTBOX_PENDING,
            attempts=0,
            available_at=available_at or datetime.now(UTC),
        )
        self._session.add(event)
        self._session.flush([event])
        event.payload = {
            **event.payload,
            "event_id": str(event.event_id),
            "outbox_id": str(event.id),
        }
        self._session.flush([event])
        return event


class TaskEventOutboxPublisher:
    def __init__(self, session: Session, task_event_bus: TaskEventBus) -> None:
        self._session = session
        self._task_event_bus = task_event_bus

    def publish_pending(self, *, limit: int = 100) -> TaskEventOutboxPublishSummary:
        events = self._pending_events(limit=limit)
        published = 0
        failed = 0
        for event in events:
            try:
                stream_id = self._task_event_bus.publish(
                    workspace_id=event.workspace_id,
                    task_id=event.task_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    event_id=str(event.event_id),
                    outbox_id=str(event.id),
                )
            except Exception as exc:
                self._record_failure(event, exc)
                failed += 1
                continue
            self._record_published(event, stream_id=stream_id)
            published += 1
        return TaskEventOutboxPublishSummary(
            scanned=len(events),
            published=published,
            failed=failed,
        )

    def _pending_events(self, *, limit: int) -> list[TaskEventOutbox]:
        now = datetime.now(UTC)
        statement = (
            select(TaskEventOutbox)
            .where(
                TaskEventOutbox.status == TASK_EVENT_OUTBOX_PENDING,
                TaskEventOutbox.available_at <= now,
            )
            .order_by(TaskEventOutbox.available_at.asc(), TaskEventOutbox.created_at.asc())
            .limit(max(1, limit))
            .with_for_update(skip_locked=True)
        )
        return list(self._session.scalars(statement).all())

    def _record_published(self, event: TaskEventOutbox, *, stream_id: str) -> None:
        event.status = TASK_EVENT_OUTBOX_PUBLISHED
        event.published_at = datetime.now(UTC)
        event.stream_id = stream_id
        event.last_error = None
        self._session.add(event)
        WebhookDeliveryService(self._session).enqueue_event(
            workspace_id=event.workspace_id,
            event_type=event.event_type,
            payload=event.payload,
            event_id=str(event.event_id),
        )
        self._session.commit()

    def _record_failure(self, event: TaskEventOutbox, exc: Exception) -> None:
        event.attempts += 1
        event.last_error = _truncate_error(exc)
        event.status = TASK_EVENT_OUTBOX_PENDING
        self._session.add(event)
        self._session.commit()


def _truncate_error(exc: Exception) -> str:
    error = str(exc) or exc.__class__.__name__
    return error[:TASK_EVENT_OUTBOX_ERROR_MAX_LENGTH]
