from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.rate_limits.service import RedisFixedWindowRateLimiter
from backend.app.secrets.service import SecretEncryptionService
from backend.app.webhooks.constants import (
    WEBHOOK_DELIVERY_MAX_ATTEMPTS,
    WEBHOOK_DELIVERY_TIMEOUT_SECONDS,
    WEBHOOK_ERROR_MAX_LENGTH,
    WEBHOOK_REPLAY_COOLDOWN_SECONDS,
    WEBHOOK_REPLAY_WORKSPACE_LIMIT,
    WEBHOOK_REPLAY_WORKSPACE_WINDOW_SECONDS,
    WEBHOOK_RETRY_BASE_DELAY_SECONDS,
)
from backend.app.webhooks.http_client import (
    UrllibWebhookHttpClient,
    WebhookHttpClient,
    WebhookHttpResponse,
)
from backend.app.webhooks.models import WebhookDeliveryAttempt, WebhookSubscription
from backend.app.webhooks.scheduler import WebhookDeliveryScheduler
from backend.app.webhooks.utils import (
    _canonical_json,
    _matches_event,
    _metadata_datetime,
    _safe_headers,
    _signed_headers,
    _snippet,
    _truncate,
    redact_webhook_sensitive_fields,
)
from backend.app.workers.queue.redis_queue import RedisQueue


class WebhookDeliveryReplayError(ValueError):
    """Raised when a delivery attempt cannot be safely replayed."""


class WebhookDeliveryReplayRateLimitError(WebhookDeliveryReplayError):
    """Raised when replay is rate-limited or within its cooldown window."""

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class WebhookDeliveryService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService | None = None,
        http_client: WebhookHttpClient | None = None,
    ) -> None:
        self._session = session
        self._secret_service = secret_service
        self._http_client = http_client or UrllibWebhookHttpClient()

    def enqueue_event(
        self,
        *,
        workspace_id: UUID,
        event_type: str,
        payload: dict[str, object],
        event_id: str | None = None,
        available_at: datetime | None = None,
    ) -> list[WebhookDeliveryAttempt]:
        subscriptions = self._matching_subscriptions(
            workspace_id=workspace_id,
            event_type=event_type,
        )
        now = datetime.now(UTC)
        delivery_event_id = event_id or str(uuid4())
        attempts = [
            WebhookDeliveryAttempt(
                workspace_id=workspace_id,
                subscription_id=subscription.id,
                event_id=delivery_event_id,
                event_type=event_type,
                payload=dict(payload),
                status="pending",
                attempt_count=0,
                max_attempts=WEBHOOK_DELIVERY_MAX_ATTEMPTS,
                available_at=available_at or now,
                dead_letter_metadata={},
                response_headers={},
                signature_verified=False,
            )
            for subscription in subscriptions
        ]
        self._session.add_all(attempts)
        if attempts:
            self._session.flush(attempts)
        return attempts

    def replay_attempt(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        delivery_attempt_id: UUID,
        queue: RedisQueue,
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

        subscription = self._session.scalar(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.id == subscription_id,
            )
        )
        if subscription is None:
            raise ValueError("Webhook subscription not found")
        if subscription.status != "active":
            raise WebhookDeliveryReplayError("Webhook subscription is disabled")
        if attempt.status == "delivering":
            raise WebhookDeliveryReplayError("Webhook delivery attempt is already delivering")

        if attempt.status != "queued":
            now = datetime.now(UTC)
            self._enforce_replay_cooldown(attempt=attempt, now=now)
            self._enforce_workspace_replay_rate_limit(queue=queue, workspace_id=workspace_id)
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
            WebhookDeliveryScheduler(self._session).enqueue_attempt(
                queue=queue,
                attempt=attempt,
                now=now,
            )

        self._session.commit()
        self._session.refresh(attempt)
        return attempt

    def deliver(self, *, workspace_id: UUID, delivery_attempt_id: UUID) -> WebhookDeliveryAttempt:
        attempt = self._session.scalar(
            select(WebhookDeliveryAttempt).where(
                WebhookDeliveryAttempt.workspace_id == workspace_id,
                WebhookDeliveryAttempt.id == delivery_attempt_id,
            )
        )
        if attempt is None:
            raise ValueError("Webhook delivery attempt not found")
        subscription = self._session.scalar(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.id == attempt.subscription_id,
            )
        )
        if subscription is None or subscription.status != "active":
            self._mark_dead_lettered(
                attempt,
                error="Webhook subscription is disabled or missing",
                status_code=None,
                response_body=None,
                response_headers={},
            )
            self._session.commit()
            self._session.refresh(attempt)
            return attempt

        attempt.status = "delivering"
        attempt.attempt_count += 1
        attempt.last_error = None
        attempt.last_status_code = None
        attempt.response_body_snippet = None
        attempt.response_headers = {}
        self._session.flush([attempt])

        body = _canonical_json(
            {
                "id": attempt.event_id,
                "type": attempt.event_type,
                "workspace_id": str(attempt.workspace_id),
                "delivery_attempt_id": str(attempt.id),
                "attempt": attempt.attempt_count,
                "data": redact_webhook_sensitive_fields(attempt.payload),
                "created_at": attempt.created_at.isoformat(),
            }
        )
        secret = self._signing_secret(subscription)
        timestamp = str(int(time.time()))
        headers = _signed_headers(
            body=body,
            secret=secret,
            timestamp=timestamp,
            event_id=attempt.event_id,
            event_type=attempt.event_type,
            delivery_attempt_id=str(attempt.id),
        )
        try:
            response = self._http_client.post(
                url=subscription.target_url,
                body=body,
                headers=headers,
                timeout_seconds=WEBHOOK_DELIVERY_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self._record_failure(
                attempt=attempt,
                subscription=subscription,
                error=str(exc) or exc.__class__.__name__,
                status_code=None,
                response_body=None,
                response_headers={},
            )
        else:
            if 200 <= response.status_code < 300:
                self._record_success(attempt=attempt, subscription=subscription, response=response)
            else:
                self._record_failure(
                    attempt=attempt,
                    subscription=subscription,
                    error=f"Webhook endpoint returned HTTP {response.status_code}",
                    status_code=response.status_code,
                    response_body=response.body,
                    response_headers=response.headers,
                )
        self._session.commit()
        self._session.refresh(attempt)
        return attempt

    def _matching_subscriptions(
        self,
        *,
        workspace_id: UUID,
        event_type: str,
    ) -> list[WebhookSubscription]:
        subscriptions = self._session.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.status == "active",
            )
        ).all()
        return [
            subscription
            for subscription in subscriptions
            if _matches_event(subscription.event_types, event_type)
        ]

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
        queue: RedisQueue,
        workspace_id: UUID,
    ) -> None:
        decision = RedisFixedWindowRateLimiter(
            queue.redis,
            key_prefix=queue.keys.prefix,
        ).check(
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

    def _signing_secret(self, subscription: WebhookSubscription) -> str:
        if self._secret_service is None:
            raise ValueError("Webhook signing secret service is required")
        payload = self._secret_service.decrypt_payload(subscription.encrypted_signing_secret)
        secret = payload.get("signing_secret")
        if not isinstance(secret, str) or not secret:
            raise ValueError("Webhook signing secret is missing")
        return secret

    def _record_success(
        self,
        *,
        attempt: WebhookDeliveryAttempt,
        subscription: WebhookSubscription,
        response: WebhookHttpResponse,
    ) -> None:
        now = datetime.now(UTC)
        attempt.status = "succeeded"
        attempt.delivered_at = now
        attempt.next_retry_at = None
        attempt.last_error = None
        attempt.last_status_code = response.status_code
        attempt.response_body_snippet = _snippet(response.body)
        attempt.response_headers = _safe_headers(response.headers)
        attempt.signature_verified = True
        subscription.last_success_at = now
        subscription.last_failure_message = None

    def _record_failure(
        self,
        *,
        attempt: WebhookDeliveryAttempt,
        subscription: WebhookSubscription,
        error: str,
        status_code: int | None,
        response_body: str | None,
        response_headers: dict[str, str],
    ) -> None:
        now = datetime.now(UTC)
        subscription.last_failure_at = now
        subscription.last_failure_message = _truncate(error, 1000)
        if attempt.attempt_count >= attempt.max_attempts:
            self._mark_dead_lettered(
                attempt,
                error=error,
                status_code=status_code,
                response_body=response_body,
                response_headers=response_headers,
            )
            return
        delay = WEBHOOK_RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempt.attempt_count - 1))
        retry_at = now + timedelta(seconds=delay)
        attempt.status = "retrying"
        attempt.available_at = retry_at
        attempt.next_retry_at = retry_at
        attempt.last_error = _truncate(error, WEBHOOK_ERROR_MAX_LENGTH)
        attempt.last_status_code = status_code
        attempt.response_body_snippet = _snippet(response_body)
        attempt.response_headers = _safe_headers(response_headers)

    def _mark_dead_lettered(
        self,
        attempt: WebhookDeliveryAttempt,
        *,
        error: str,
        status_code: int | None,
        response_body: str | None,
        response_headers: dict[str, str],
    ) -> None:
        now = datetime.now(UTC)
        attempt.status = "dead_lettered"
        attempt.dead_lettered_at = now
        attempt.next_retry_at = None
        attempt.last_error = _truncate(error, WEBHOOK_ERROR_MAX_LENGTH)
        attempt.last_status_code = status_code
        attempt.response_body_snippet = _snippet(response_body)
        attempt.response_headers = _safe_headers(response_headers)
        attempt.dead_letter_metadata = {
            "reason": _truncate(error, WEBHOOK_ERROR_MAX_LENGTH),
            "attempt_count": attempt.attempt_count,
            "max_attempts": attempt.max_attempts,
            "last_status_code": status_code,
            "dead_lettered_at": now.isoformat(),
        }
