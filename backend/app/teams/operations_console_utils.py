from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from backend.app.core.typing import (
    datetime_or_none,
    dict_or_empty,
    int_or_zero,
    positive_int_or_default,
    string_list,
    uuid_or_none,
)
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.teams.team_policy_visibility import visible_task_policy


def _dict(value: object) -> dict[str, object]:
    return dict_or_empty(value)


def _string_list(value: object) -> list[str]:
    return string_list(value)


def _unique_strings(values: Iterable[object]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if not isinstance(value, str) or value in unique:
            continue
        unique.append(value)
    return unique


def _uuid_or_none(value: object) -> UUID | None:
    return uuid_or_none(value)


def _redacted_dict_or_none(value: dict[str, object] | None) -> dict[str, object] | None:
    return redact_sensitive_payload(dict(value)) if isinstance(value, dict) else None


def _positive_int(value: object, default: int) -> int:
    return positive_int_or_default(value, default)


def _int_value(value: object) -> int:
    return int_or_zero(value)


def _datetime_or_none(value: object) -> datetime | None:
    return datetime_or_none(value)


def _visible_task_policy(value: object) -> dict[str, object]:
    return visible_task_policy(value)


def _preview(value: str, *, max_chars: int = 240) -> str:
    text = " ".join(value.split())
    text = redact_sensitive_text(text)
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."
