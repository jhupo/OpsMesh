"""Transactional job outbox; Redis never observes an uncommitted business change."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.runtime.queues.contracts import JobPayload
from opsmesh.runtime.queues.models import QueueDispatch
from opsmesh.runtime.queues.service import RedisQueue

logger = logging.getLogger(__name__)


def stage_job(session: Session, job: JobPayload) -> None:
    session.add(
        QueueDispatch(
            workspace_id=job.workspace_id,
            created_by_user_id=job.requested_by_user_id,
            payload=job.with_trace_context().model_dump(mode="json"),
            available_at=datetime.now(UTC),
        )
    )


class QueueDispatchPublisher:
    def __init__(self, session: Session, queue: RedisQueue) -> None:
        self.session = session
        self.queue = queue

    def publish_pending(self, *, limit: int = 100, workspace_id: UUID | None = None) -> int:
        now = datetime.now(UTC)
        statement = select(QueueDispatch)
        if workspace_id is not None:
            statement = statement.where(QueueDispatch.workspace_id == workspace_id)
        rows = list(
            self.session.scalars(
                statement.where(
                    QueueDispatch.published_at.is_(None), QueueDispatch.available_at <= now
                )
                .order_by(QueueDispatch.available_at, QueueDispatch.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        published = 0
        for row in rows:
            try:
                job = JobPayload.model_validate(row.payload)
                if job.workspace_id != row.workspace_id:
                    raise ValueError("Queue outbox workspace mismatch")
                self.queue.enqueue(job)
            except Exception:
                row.attempts += 1
                row.available_at = now + timedelta(seconds=min(2 ** min(row.attempts, 8), 300))
                logger.error(
                    "Queue outbox delivery failed", extra={"workspace_id": str(row.workspace_id)}
                )
            else:
                row.published_at = now
                published += 1
        self.session.commit()
        return published
