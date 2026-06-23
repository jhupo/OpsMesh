from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.app.model_providers.model_api import canonical_model_api

_SENSITIVE_METADATA_KEYS = {
    "api_key",
    "authorization",
    "base_url",
    "bearer",
    "encrypted_api_key",
    "password",
    "secret",
    "token",
}


def sanitize_budget_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, object] = {}
    for key, item in value.items():
        normalized_key = str(key)
        if normalized_key.lower() in _SENSITIVE_METADATA_KEYS:
            continue
        if normalized_key == "model_api":
            model_api = canonical_model_api(item)
            if model_api is not None:
                sanitized[normalized_key] = model_api
            continue
        sanitized[normalized_key] = _sanitize_budget_value(item)
    return sanitized


def budget_is_exhausted(value: object, *, now: datetime | None = None) -> bool:
    metadata = value if isinstance(value, dict) else {}
    if not metadata:
        return False

    exhausted_until = _parse_datetime(metadata.get("exhausted_until"))
    if exhausted_until is not None:
        return (now or datetime.now(UTC)) < exhausted_until

    if metadata.get("exhausted") is True:
        return True

    status = metadata.get("status")
    if isinstance(status, str) and status.lower() == "exhausted":
        return True

    remaining = _number(metadata.get("remaining"))
    if remaining is not None and remaining <= 0:
        return True
    remaining_by_unit = _dict(metadata.get("remaining"))
    if remaining_by_unit and any(
        (amount := _number(value)) is not None and amount <= 0
        for value in remaining_by_unit.values()
    ):
        return True

    limits = _dict(metadata.get("limits"))
    usage = _dict(metadata.get("usage"))
    if not limits or not usage:
        return False

    for key, limit in limits.items():
        limit_number = _number(limit)
        usage_number = _number(usage.get(key))
        if limit_number is not None and usage_number is not None and usage_number >= limit_number:
            return True
    return False


def _sanitize_budget_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_sanitize_budget_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sanitize_budget_value(item)
            for key, item in value.items()
            if str(key).lower() not in _SENSITIVE_METADATA_KEYS
        }
    return str(value)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
