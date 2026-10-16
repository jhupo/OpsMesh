"""Neutral value contracts: JSON values, UTC time and identifier construction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeAlias
from uuid import UUID, uuid4

JsonValue: TypeAlias = dict[str, "JsonValue"] | list["JsonValue"] | str | int | float | bool | None
JsonObject: TypeAlias = dict[str, JsonValue]
Identifier = UUID


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


def ensure_aware_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and convert aware values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def new_identifier() -> Identifier:
    """Fresh identifier for caller-owned entities and receipts."""
    return uuid4()
