from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

RUNTIME_CAPACITY_KEYS = ("max_concurrent_jobs", "max_jobs", "slots", "capacity_slots")


def positive_int(value: object, fallback: int) -> int:
    parsed = positive_int_or_none(value)
    return parsed if parsed is not None else max(1, fallback)


def positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return 0
    return 0


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def age_seconds(now: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((ensure_aware_utc(now) - ensure_aware_utc(value)).total_seconds()))


def capacity_slots_from_metadata(capabilities: Mapping[str, object]) -> int:
    for key in RUNTIME_CAPACITY_KEYS:
        value = positive_int_or_none(capabilities.get(key))
        if value is not None:
            return value
    return 1
