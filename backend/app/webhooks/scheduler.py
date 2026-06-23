from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.webhooks.models import WebhookDeliveryAttempt
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue


@dataclass(frozen=True)
class WebhookDeliveryScheduleSummary:
    scanned: int
    enqueued: int
    skipped: int


class WebhookDeliveryScheduler:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_due(
        self,
        *,
        queue: RedisQueue,
        limit: int = 100,
    ) -> WebhookDeliveryScheduleSummary:
        attempts = self._due_attempts(limit=limit)
        enqueued = 0
        skipped = 0
        now = datetime.now(UTC)
        for attempt in attempts:
            if self.enqueue_attempt(queue=queue, attempt=attempt, now=now):
                enqueued += 1
            else:
                skipped += 1
        self._session.commit()
        return WebhookDeliveryScheduleSummary(
            scanned=len(attempts),
            enqueued=enqueued,
            skipped=skipped,
        )

    def enqueue_attempt(
        self,
        *,
        queue: RedisQueue,
        attempt: WebhookDeliveryAttempt,
        now: datetime | None = None,
    ) -> bool:
        job = JobPayload(
            workspace_id=attempt.workspace_id,
            job_type=JobType.WEBHOOK_DELIVERY,
            resource_id=attempt.id,
            idempotency_key=(
                f"webhook.delivery:{attempt.workspace_id}:{attempt.id}:{attempt.attempt_count}"
            ),
            max_attempts=1,
            routing={"subscription_id": str(attempt.subscription_id)},
        )
        if not queue.enqueue(job):
            return False
        attempt.status = "queued"
        attempt.queued_at = now or datetime.now(UTC)
        attempt.job_id = job.job_id
        self._session.add(attempt)
        return True

    def _due_attempts(self, *, limit: int) -> list[WebhookDeliveryAttempt]:
        now = datetime.now(UTC)
        statement = (
            select(WebhookDeliveryAttempt)
            .where(
                WebhookDeliveryAttempt.status.in_(["pending", "retrying"]),
                WebhookDeliveryAttempt.available_at <= now,
            )
            .order_by(
                WebhookDeliveryAttempt.available_at.asc(),
                WebhookDeliveryAttempt.created_at.asc(),
            )
            .limit(max(1, limit))
            .with_for_update(skip_locked=True)
        )
        return list(self._session.scalars(statement).all())
