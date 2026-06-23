from datetime import UTC, datetime, timedelta

from backend.app.webhooks.constants import (
    WEBHOOK_ERROR_MAX_LENGTH,
    WEBHOOK_RETRY_BASE_DELAY_SECONDS,
)
from backend.app.webhooks.http_client import WebhookHttpResponse
from backend.app.webhooks.models import WebhookDeliveryAttempt, WebhookSubscription
from backend.app.webhooks.utils import _safe_headers, _snippet, _truncate


class WebhookDeliveryStateRecorder:
    def start_delivery(self, attempt: WebhookDeliveryAttempt) -> None:
        attempt.status = "delivering"
        attempt.attempt_count += 1
        attempt.last_error = None
        attempt.last_status_code = None
        attempt.response_body_snippet = None
        attempt.response_headers = {}

    def record_success(
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

    def record_failure(
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
            self.mark_dead_lettered(
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

    def mark_dead_lettered(
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
