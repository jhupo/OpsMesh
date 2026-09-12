from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import overload
from uuid import UUID


def dict_or_empty(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def dict_or_none(value: object) -> dict[str, object] | None:
    return dict(value) if isinstance(value, dict) else None


def dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def positive_int_or_default(value: object, default: int) -> int:
    parsed = positive_int_or_none(value)
    return parsed if parsed is not None else default


def positive_int_or_none(value: object) -> int | None:
    """Parse a positive integer from persisted or transport data."""
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
    """Normalize a counter value without allowing booleans or negatives."""
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


def uuid_or_none(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def datetime_or_none(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def counts_by_value(values: Iterable[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def dedupe_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def age_seconds(now: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((ensure_aware_utc(now) - ensure_aware_utc(value)).total_seconds()))


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def string_list_or_single(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    return string_list(value)


def string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


@overload
def json_safe_payload(value: dict[str, object]) -> dict[str, object]: ...


@overload
def json_safe_payload(value: object) -> object: ...


def json_safe_payload(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe_payload(item) for item in value]
    return str(value)
