"""OpsMesh webhook authentication shared by publisher and external connectors."""

import hashlib
import hmac
import time
from collections.abc import Mapping
from uuid import UUID

from opsmesh_plugin_sdk.contracts import AutomationDelivery


def signed_headers(
    *,
    body: bytes,
    secret: str,
    timestamp: str,
    event_id: str,
    event_type: str,
    delivery_attempt_id: str,
) -> dict[str, str]:
    payload = b".".join([timestamp.encode("ascii"), event_id.encode("utf-8"), body])
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-OpsMesh-Event-Id": event_id,
        "X-OpsMesh-Event-Type": event_type,
        "X-OpsMesh-Timestamp": timestamp,
        "X-OpsMesh-Delivery-Attempt-Id": delivery_attempt_id,
        "X-OpsMesh-Signature": f"sha256={digest}",
    }


def verify_delivery(
    body: bytes,
    headers: Mapping[str, str],
    *,
    secret: str,
    max_age_seconds: int = 300,
    now: float | None = None,
) -> str:
    """Return the verified event ID; the receiver must durably deduplicate it before sending."""
    values = {key.lower(): value for key, value in headers.items()}
    try:
        timestamp = values["x-opsmesh-timestamp"]
        event_id = values["x-opsmesh-event-id"]
        supplied = values["x-opsmesh-signature"]
        age = abs((time.time() if now is None else now) - int(timestamp))
        if not secret or not event_id or max_age_seconds <= 0 or age > max_age_seconds:
            raise ValueError("Delivery outside authentication window")
        expected = signed_headers(
            body=body,
            secret=secret,
            timestamp=timestamp,
            event_id=event_id,
            event_type=values.get("x-opsmesh-event-type", ""),
            delivery_attempt_id=values.get("x-opsmesh-delivery-attempt-id", ""),
        )["X-OpsMesh-Signature"]
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("Invalid delivery signature")
    except (KeyError, UnicodeError, ValueError) as exc:
        raise ValueError("Webhook authentication failed") from exc
    return event_id


def parse_automation_delivery(
    body: bytes,
    headers: Mapping[str, str],
    *,
    secret: str,
    workspace_id: UUID,
    automation_id: UUID,
    max_age_seconds: int = 300,
) -> AutomationDelivery:
    """Authenticate before parsing; callers still own durable deduplication and channel delivery."""
    event_id = verify_delivery(body, headers, secret=secret, max_age_seconds=max_age_seconds)
    delivery = AutomationDelivery.model_validate_json(body)
    if (
        delivery.id != event_id
        or delivery.workspace_id != workspace_id
        or delivery.data.automation_id != automation_id
    ):
        raise ValueError("Delivery does not match the configured workspace and automation")
    return delivery
