from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

from backend.app.webhooks.constants import (
    WEBHOOK_REDACTED_FIELD_NAMES,
    WEBHOOK_RESPONSE_SNIPPET_MAX_LENGTH,
    WEBHOOK_SECRET_MASK,
)


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
