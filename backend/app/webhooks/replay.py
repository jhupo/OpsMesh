import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.rate_limits.service import FixedWindowRateLimiter
from backend.app.webhooks.constants import (
    WEBHOOK_REPLAY_COOLDOWN_SECONDS,
    WEBHOOK_REPLAY_WORKSPACE_LIMIT,
    WEBHOOK_REPLAY_WORKSPACE_WINDOW_SECONDS,
)
from backend.app.webhooks.models import WebhookDeliveryAttempt, WebhookSubscription
from backend.app.webhooks.scheduler import WebhookDeliveryScheduler
from backend.app.webhooks.utils import _metadata_datetime
from backend.app.workers.queue.redis_queue import RedisQueue


class WebhookDeliveryReplayError(ValueError):
    """Raised when a delivery attempt cannot be safely replayed."""


class WebhookDeliveryReplayRateLimitError(WebhookDeliveryReplayError):
    """Raised when replay is rate-limited or within its cooldown window."""

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class WebhookDeliveryReplayService:
    def __init__(self, session: Session, rate_limiter: FixedWindowRateLimiter) -> None:
        self._session = session
        self._rate_limiter = rate_limiter

    def replay_attempt(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        delivery_attempt_id: UUID,
        queue: RedisQueue,
    ) -> WebhookDeliveryAttempt:
        attempt = self._attempt(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
            delivery_attempt_id=delivery_attempt_id,
        )
        subscription = self._subscription(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
        )
        if subscription.status != "active":
            raise WebhookDeliveryReplayError("Webhook subscription is disabled")
        if attempt.status == "delivering":
            raise WebhookDeliveryReplayError("Webhook delivery attempt is already delivering")

        if attempt.status != "queued":
            now = datetime.now(UTC)
            self._enforce_replay_cooldown(attempt=attempt, now=now)
            self._enforce_workspace_replay_rate_limit(workspace_id=workspace_id)
            self._reset_attempt(attempt, now=now)
            WebhookDeliveryScheduler(self._session).enqueue_attempt(
                queue=queue,
                attempt=attempt,
                now=now,
            )

        self._session.commit()
        self._session.refresh(attempt)
        return attempt

    def _attempt(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        delivery_attempt_id: UUID,
    ) -> WebhookDeliveryAttempt:
        attempt = self._session.scalar(
            select(WebhookDeliveryAttempt).where(
                WebhookDeliveryAttempt.workspace_id == workspace_id,
                WebhookDeliveryAttempt.subscription_id == subscription_id,
                WebhookDeliveryAttempt.id == delivery_attempt_id,
            )
        )
        if attempt is None:
            raise ValueError("Webhook delivery attempt not found")
        return attempt

    def _subscription(self, *, workspace_id: UUID, subscription_id: UUID) -> WebhookSubscription:
        subscription = self._session.scalar(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.id == subscription_id,
            )
        )
        if subscription is None:
            raise ValueError("Webhook subscription not found")
        return subscription

    def _reset_attempt(self, attempt: WebhookDeliveryAttempt, *, now: datetime) -> None:
        previous_status = attempt.status
        attempt.status = "pending"
        attempt.available_at = now
        attempt.queued_at = None
        attempt.job_id = None
        attempt.delivered_at = None
        attempt.dead_lettered_at = None
        attempt.next_retry_at = None
        attempt.last_error = None
        attempt.last_status_code = None
        attempt.response_body_snippet = None
        attempt.response_headers = {}
        attempt.signature_verified = False
        attempt.dead_letter_metadata = {
            "replayed_at": now.isoformat(),
            "replayed_from_status": previous_status,
            "replayed_attempt_count": attempt.attempt_count,
        }
        self._session.flush([attempt])

    def _enforce_replay_cooldown(
        self,
        *,
        attempt: WebhookDeliveryAttempt,
        now: datetime,
    ) -> None:
        replayed_at = _metadata_datetime(attempt.dead_letter_metadata, "replayed_at")
        if replayed_at is None:
            return
        elapsed = (now - replayed_at).total_seconds()
        retry_after_seconds = WEBHOOK_REPLAY_COOLDOWN_SECONDS - int(elapsed)
        if retry_after_seconds <= 0:
            return
        raise WebhookDeliveryReplayRateLimitError(
            "Webhook delivery attempt replay is cooling down",
            retry_after_seconds=retry_after_seconds,
        )

    def _enforce_workspace_replay_rate_limit(
        self,
        *,
        workspace_id: UUID,
    ) -> None:
        decision = self._rate_limiter.check(
            identifier=f"webhook-replay:{workspace_id}",
            limit=WEBHOOK_REPLAY_WORKSPACE_LIMIT,
            window_seconds=WEBHOOK_REPLAY_WORKSPACE_WINDOW_SECONDS,
        )
        if decision.allowed:
            return
        retry_after_seconds = max(1, decision.reset_epoch_seconds - int(time.time()))
        raise WebhookDeliveryReplayRateLimitError(
            "Webhook delivery attempt replay rate limit exceeded",
            retry_after_seconds=retry_after_seconds,
        )
