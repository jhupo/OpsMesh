import hashlib
import hmac
import json
import time

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.integrations.webhooks.models import (
    WebhookDeliveryAttempt,
    WebhookSubscription,
)


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
        [timestamp.encode("ascii"), event_id.encode("utf-8"), body]
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


class WebhookDeliverySigner:
    def __init__(self, secret_service: SecretEncryptionService | None) -> None:
        self._secret_service = secret_service

    def build_request(
        self,
        *,
        attempt: WebhookDeliveryAttempt,
        subscription: WebhookSubscription,
    ) -> tuple[bytes, dict[str, str]]:
        body = _canonical_json(
            {
                "id": attempt.event_id,
                "type": attempt.event_type,
                "workspace_id": str(attempt.workspace_id),
                "delivery_attempt_id": str(attempt.id),
                "attempt": attempt.attempt_count,
                "data": redact_sensitive_payload(attempt.payload),
                "created_at": attempt.created_at.isoformat(),
            }
        )
        timestamp = str(int(time.time()))
        return body, _signed_headers(
            body=body,
            secret=self._signing_secret(subscription),
            timestamp=timestamp,
            event_id=attempt.event_id,
            event_type=attempt.event_type,
            delivery_attempt_id=str(attempt.id),
        )

    def _signing_secret(self, subscription: WebhookSubscription) -> str:
        if self._secret_service is None:
            raise ValueError("Webhook signing secret service is required")
        payload = self._secret_service.decrypt_payload(subscription.encrypted_signing_secret)
        secret = payload.get("signing_secret")
        if not isinstance(secret, str) or not secret:
            raise ValueError("Webhook signing secret is missing")
        return secret
