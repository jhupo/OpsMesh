"""HMAC verification authenticates timestamp, delivery identifier and raw body."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import NoReturn

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.modules.triggers.api import TriggerDefinition


def reject_constant(value: str) -> NoReturn:
    raise ValueError("Non-finite JSON constants are not supported")


def authenticate_webhook(
    definition: TriggerDefinition,
    secret: str,
    body: bytes,
    *,
    timestamp: str,
    delivery_id: str,
    signature: str,
) -> OpsMeshEvent:
    if len(body) > 65536:
        raise OpsMeshError("INPUT_TOO_LARGE", "Webhook body exceeds 64 KiB")
    if (
        not delivery_id
        or len(delivery_id) > 200
        or len(timestamp) > 20
        or len(signature) != 71
        or not signature.isascii()
    ):
        raise OpsMeshError("UNAUTHORIZED", "Invalid webhook authentication")
    try:
        epoch = int(timestamp)
    except ValueError as exc:
        raise OpsMeshError("UNAUTHORIZED", "Invalid webhook authentication") from exc
    if abs(time.time() - epoch) > 300:
        raise OpsMeshError("UNAUTHORIZED", "Webhook authentication expired")
    expected = (
        "sha256="
        + hmac.new(
            secret.encode(), f"{timestamp}.{delivery_id}.".encode() + body, hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(expected, signature):
        raise OpsMeshError("UNAUTHORIZED", "Invalid webhook authentication")
    try:
        payload = json.loads(body, parse_constant=reject_constant)
        if not isinstance(payload, dict):
            raise ValueError("Expected an object")
        return OpsMeshEvent(
            id=delivery_id,
            source=definition.source,
            type=definition.event_type,
            occurred_at=datetime.fromtimestamp(epoch, UTC),
            payload=payload,
        )
    except (ValueError, TypeError, RecursionError) as exc:
        raise OpsMeshError("VALIDATION_ERROR", "Webhook body must be a JSON object") from exc
