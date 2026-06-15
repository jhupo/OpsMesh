from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.rate_limits.service import RedisFixedWindowRateLimiter
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import EgressUrlPolicy, validate_egress_url
from backend.app.webhooks.models import WebhookDeliveryAttempt, WebhookSubscription
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue

WEBHOOK_URL_POLICY = EgressUrlPolicy(allowed_schemes=frozenset({"http", "https"}))
WEBHOOK_SECRET_MASK = "[redacted]"
WEBHOOK_DELIVERY_TIMEOUT_SECONDS = 10
WEBHOOK_DELIVERY_MAX_ATTEMPTS = 3
WEBHOOK_RETRY_BASE_DELAY_SECONDS = 60
WEBHOOK_ERROR_MAX_LENGTH = 2_000
WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH = 1_000
WEBHOOK_REPLAY_COOLDOWN_SECONDS = 60
WEBHOOK_REPLAY_WORKSPACE_LIMIT = 10
WEBHOOK_REPLAY_WORKSPACE_WINDOW_SECONDS = 60
WEBHOOK_REDACTED_FIELD_NAMES = frozenset(
    {
        "ciphertext",
        "encrypted_secret_payload",
        "encrypted_signing_secret",
        "signing_secret",
    }
)


class WebhookDeliveryReplayError(ValueError):
    """Raised when a delivery attempt cannot be safely replayed."""


class WebhookDeliveryReplayRateLimitError(WebhookDeliveryReplayError):
    """Raised when replay is rate-limited or within its cooldown window."""

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class WebhookHttpClient(Protocol):
    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> WebhookHttpResponse: ...


@dataclass(frozen=True)
class WebhookHttpResponse:
    status_code: int
    body: str
    headers: dict[str, str]


@dataclass(frozen=True)
class WebhookDeliveryScheduleSummary:
    scanned: int
    enqueued: int
    skipped: int


class UrllibWebhookHttpClient:
    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> WebhookHttpResponse:
        request = Request(
            url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                return WebhookHttpResponse(
                    status_code=int(response.status),
                    body=response.read(WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH).decode(
                        "utf-8",
                        errors="replace",
                    ),
                    headers={key: value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            body_text = exc.read(WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH).decode(
                "utf-8",
                errors="replace",
            )
            return WebhookHttpResponse(
                status_code=int(exc.code),
                body=body_text,
                headers={key: value for key, value in exc.headers.items()},
            )
        except URLError as exc:
            raise ConnectionError(str(exc.reason)) from exc


class WebhookSubscriptionService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
        egress_policy: EgressUrlPolicy = WEBHOOK_URL_POLICY,
    ) -> None:
        self._session = session
        self._secret_service = secret_service
        self._egress_policy = egress_policy

    def create(
        self,
        *,
        workspace_id: UUID,
        created_by_user_id: UUID,
        name: str,
        target_url: str,
        event_types: list[str],
        signing_secret: str,
    ) -> WebhookSubscription:
        encrypted = self._secret_service.encrypt_payload({"signing_secret": signing_secret})
        subscription = WebhookSubscription(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            name=name,
            target_url=validate_egress_url(target_url, policy=self._egress_policy),
            event_types=_normalized_event_types(event_types),
            encrypted_signing_secret=encrypted.ciphertext,
            signing_secret_fingerprint=encrypted.fingerprint,
            encryption_key_id=encrypted.key_id,
            status="active",
        )
        self._session.add(subscription)
        self._session.commit()
        self._session.refresh(subscription)
        return subscription

    def list(
        self,
        *,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[WebhookSubscription], int]:
        statement = select(WebhookSubscription).where(
            WebhookSubscription.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(WebhookSubscription.status == status)
        statement = statement.order_by(
            WebhookSubscription.created_at.desc(),
            WebhookSubscription.id.desc(),
        )
        return page_scalars(self._session, statement, page)

    def update(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        name: str | None = None,
        target_url: str | None = None,
        event_types: list[str] | None = None,
    ) -> WebhookSubscription:
        subscription = self._require(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
        )
        if name is not None:
            subscription.name = name
        if target_url is not None:
            subscription.target_url = validate_egress_url(
                target_url,
                policy=self._egress_policy,
            )
        if event_types is not None:
            subscription.event_types = _normalized_event_types(event_types)
        self._session.commit()
        self._session.refresh(subscription)
        return subscription

    def rotate_signing_secret(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        signing_secret: str,
    ) -> WebhookSubscription:
        subscription = self._require(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
        )
        encrypted = self._secret_service.encrypt_payload({"signing_secret": signing_secret})
        subscription.encrypted_signing_secret = encrypted.ciphertext
        subscription.signing_secret_fingerprint = encrypted.fingerprint
        subscription.encryption_key_id = encrypted.key_id
        self._session.commit()
        self._session.refresh(subscription)
        return subscription

    def disable(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
    ) -> WebhookSubscription:
        subscription = self._require(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
        )
        if subscription.status != "disabled":
            subscription.status = "disabled"
            subscription.disabled_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(subscription)
        return subscription

    def _require(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
    ) -> WebhookSubscription:
        subscription = self._session.scalar(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.id == subscription_id,
            )
        )
        if subscription is None:
            raise ValueError("Webhook subscription not found")
        return subscription


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
                f"webhook.delivery:{attempt.workspace_id}:{attempt.id}:"
                f"{attempt.attempt_count}"
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


def _normalized_event_types(event_types: list[str]) -> list[str]:
    normalized = sorted({item.strip() for item in event_types if item.strip()})
    if not normalized:
        raise ValueError("At least one event type is required")
    return ["*"] if "*" in normalized else normalized


def _matches_event(event_types: list[str], event_type: str) -> bool:
    return "*" in event_types or event_type in event_types


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _signed_headers(
    *,
    body: bytes,
    secret: str,
    timestamp: str,
    event_id: str,
    event_type: str,
    delivery_attempt_id: str,
) -> dict[str, str]:
    signed_payload = b".".join(
        [
            timestamp.encode("ascii"),
            event_id.encode("utf-8"),
            body,
        ]
    )
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-OpsMesh-Event-Id": event_id,
        "X-OpsMesh-Event-Type": event_type,
        "X-OpsMesh-Timestamp": timestamp,
        "X-OpsMesh-Delivery-Attempt-Id": delivery_attempt_id,
        "X-OpsMesh-Signature": f"sha256={digest}",
    }


def _snippet(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH]


def _truncate(value: str, max_length: int) -> str:
    return value[:max_length]


def _safe_headers(headers: dict[str, str]) -> dict[str, object]:
    sensitive = {"authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token"}
    safe: dict[str, object] = {}
    for key, value in headers.items():
        safe[key] = WEBHOOK_SECRET_MASK if key.lower() in sensitive else value
    return safe


def redact_webhook_sensitive_fields(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            if key.lower() in WEBHOOK_REDACTED_FIELD_NAMES:
                redacted[key] = WEBHOOK_SECRET_MASK
            else:
                redacted[key] = redact_webhook_sensitive_fields(item)
        return redacted
    if isinstance(value, list):
        return [redact_webhook_sensitive_fields(item) for item in value]
    return value


def _metadata_datetime(metadata: dict[str, object], key: str) -> datetime | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
