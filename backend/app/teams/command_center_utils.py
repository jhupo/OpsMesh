from __future__ import annotations

from uuid import UUID

from backend.app.core.typing import dict_or_empty, int_or_zero, json_safe_payload, string_list


def _dict(value: object) -> dict[str, object]:
    return dict_or_empty(value)


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    return string_list(value)


def _int(value: object) -> int:
    return int_or_zero(value)


def _uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]


def _uuid_value(value: object) -> UUID | None:
    return value if isinstance(value, UUID) else None


def _json_safe_payload(value: object) -> object:
    return json_safe_payload(value)
