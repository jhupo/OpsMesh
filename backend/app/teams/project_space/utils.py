from __future__ import annotations

from collections import Counter
from datetime import datetime
from uuid import UUID


def counts(values: object) -> dict[str, int]:
    counted: Counter[str] = Counter(str(value) for value in values)
    return dict(sorted(counted.items()))


def list_of_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def number(value: object) -> int | float:
    return value if isinstance(value, int | float) else 0


def mark_latest(
    latest: dict[UUID, datetime],
    agent_profile_id: UUID,
    timestamp: datetime | None,
) -> None:
    if timestamp is None:
        return
    current = latest.get(agent_profile_id)
    if current is None or timestamp > current:
        latest[agent_profile_id] = timestamp
