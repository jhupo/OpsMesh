from __future__ import annotations

from uuid import UUID


def slug(value: str) -> str:
    slug_value = "".join(character.lower() if character.isalnum() else "-" for character in value)
    slug_value = "-".join(part for part in slug_value.split("-") if part)
    return slug_value or "package"


def uuid_or_none(value: object | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def int_or_default(value: object, default: int) -> int:
    return value if isinstance(value, int) else default
