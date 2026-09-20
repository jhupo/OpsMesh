import json
import time
from datetime import UTC

from opsmesh_plugin_sdk.messaging.webhooks import signed_headers

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
                "created_at": attempt.created_at.replace(tzinfo=UTC).isoformat()
                if attempt.created_at.tzinfo is None
                else attempt.created_at.isoformat(),
            }
        )
        timestamp = str(int(time.time()))
        return body, signed_headers(
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
