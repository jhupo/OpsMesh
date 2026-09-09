from __future__ import annotations

from datetime import datetime
from uuid import UUID


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
