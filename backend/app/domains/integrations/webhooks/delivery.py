from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.rate_limits import FixedWindowRateLimiter
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.capabilities.plugins.policy import plugin_resource_available
from backend.app.domains.integrations.webhooks.delivery_state import WebhookDeliveryStateRecorder
from backend.app.domains.integrations.webhooks.http_client import (
    HttpxWebhookHttpClient,
    WebhookHttpClient,
)
from backend.app.domains.integrations.webhooks.models import (
    WebhookDeliveryAttempt,
    WebhookSubscription,
)
from backend.app.domains.integrations.webhooks.policy import (
    WEBHOOK_DELIVERY_MAX_ATTEMPTS,
    WEBHOOK_DELIVERY_TIMEOUT_SECONDS,
)
from backend.app.domains.integrations.webhooks.replay import (
    WebhookDeliveryReplayError,
    WebhookDeliveryReplayRateLimitError,
    WebhookDeliveryReplayService,
)
from backend.app.domains.integrations.webhooks.signing import WebhookDeliverySigner
from backend.app.runtime.workers.queue import RedisQueue


class WebhookDeliveryService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService | None = None,
        http_client: WebhookHttpClient | None = None,
    ) -> None:
        self._session = session
        self._http_client = http_client or HttpxWebhookHttpClient()
        self._signer = WebhookDeliverySigner(secret_service)
        self._state = WebhookDeliveryStateRecorder()

    def enqueue_event(
        self,
        *,
        workspace_id: UUID,
        event_type: str,
        payload: dict[str, object],
        event_id: str | None = None,
        available_at: datetime | None = None,
        subscription_id: UUID | None = None,
    ) -> list[WebhookDeliveryAttempt]:
        subscriptions = self._matching_subscriptions(
            workspace_id=workspace_id,
            event_type=event_type,
        )
        if subscription_id is not None:
            subscriptions = [item for item in subscriptions if item.id == subscription_id]
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
        rate_limiter: FixedWindowRateLimiter,
    ) -> WebhookDeliveryAttempt:
        return WebhookDeliveryReplayService(self._session, rate_limiter).replay_attempt(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
            delivery_attempt_id=delivery_attempt_id,
            queue=queue,
        )

    def deliver(self, *, workspace_id: UUID, delivery_attempt_id: UUID) -> WebhookDeliveryAttempt:
        attempt = self._attempt(workspace_id=workspace_id, delivery_attempt_id=delivery_attempt_id)
        subscription = self._subscription(workspace_id=workspace_id, attempt=attempt)
        if (
            subscription is None
            or subscription.status != "active"
            or not plugin_resource_available(
                self._session,
                workspace_id,
                "reply_channel",
                subscription.id,
            )
        ):
            self._state.mark_dead_lettered(
                attempt,
                error="Webhook subscription is disabled or missing",
                status_code=None,
                response_body=None,
                response_headers={},
            )
            self._session.commit()
            self._session.refresh(attempt)
            return attempt

        self._state.start_delivery(attempt)
        self._session.flush([attempt])
        body, headers = self._signer.build_request(attempt=attempt, subscription=subscription)
        try:
            response = self._http_client.post(
                url=subscription.target_url,
                body=body,
                headers=headers,
                timeout_seconds=WEBHOOK_DELIVERY_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self._state.record_failure(
                attempt=attempt,
                subscription=subscription,
                error=str(exc) or exc.__class__.__name__,
                status_code=None,
                response_body=None,
                response_headers={},
            )
        else:
            if 200 <= response.status_code < 300:
                self._state.record_success(
                    attempt=attempt,
                    subscription=subscription,
                    response=response,
                )
            else:
                self._state.record_failure(
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

    def _attempt(self, *, workspace_id: UUID, delivery_attempt_id: UUID) -> WebhookDeliveryAttempt:
        attempt = self._session.scalar(
            select(WebhookDeliveryAttempt).where(
                WebhookDeliveryAttempt.workspace_id == workspace_id,
                WebhookDeliveryAttempt.id == delivery_attempt_id,
            )
        )
        if attempt is None:
            raise ValueError("Webhook delivery attempt not found")
        return attempt

    def _subscription(
        self,
        *,
        workspace_id: UUID,
        attempt: WebhookDeliveryAttempt,
    ) -> WebhookSubscription | None:
        return self._session.scalar(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.id == attempt.subscription_id,
            )
        )


__all__ = [
    "WebhookDeliveryReplayError",
    "WebhookDeliveryReplayRateLimitError",
    "WebhookDeliveryService",
]


def _matches_event(event_types: list[str], event_type: str) -> bool:
    return "*" in event_types or event_type in event_types
