from __future__ import annotations

from datetime import UTC, datetime


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def oldest_age_seconds(now: datetime, values: list[datetime]) -> int | None:
    ages = [max(0, int((now - aware_datetime(value)).total_seconds())) for value in values]
    return max(ages) if ages else None


def max_optional_int(current: object, candidate: int) -> int:
    return candidate if not isinstance(current, int) else max(current, candidate)


def non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
