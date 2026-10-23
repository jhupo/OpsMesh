"""Canonical JSON, stable digests and timestamp encoding as pure functions."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256

from opsmesh.shared.contracts import ensure_aware_utc


def json_safe_value(value: object) -> object:
    """Normalize supported scalar types; reject lossy or non-JSON values."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return encode_timestamp(value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe_value(item) for item in value]
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")


def canonical_json(payload: Mapping[str, object]) -> str:
    """Stable text form used for digests, cache keys and durable comparisons."""
    return json.dumps(
        json_safe_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def payload_digest(payload: Mapping[str, object]) -> str:
    """SHA-256 over the canonical form of a JSON object."""
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def encode_timestamp(value: datetime) -> str:
    """Serialize a datetime as an aware UTC ISO-8601 string."""
    return ensure_aware_utc(value).isoformat()


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime."""
    return ensure_aware_utc(datetime.fromisoformat(value))
