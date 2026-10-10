from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class TimelineFilters:
    limit: int = 50
    offset: int = 0
    source_type: str | None = None
    event_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    include_runs: bool = False
    include_queue: bool = True


@dataclass(frozen=True)
class TimelineEvent:
    id: str
    source_type: str
    event_type: str
    occurred_at: datetime
    resource_id: str
    message: str
    metadata: dict[str, object]


def apply_time_filters(statement: Any, column: Any, filters: TimelineFilters) -> Any:
    if filters.since is not None:
        statement = statement.where(column >= filters.since)
    if filters.until is not None:
        statement = statement.where(column <= filters.until)
    return statement


def matches_filters(event: TimelineEvent, filters: TimelineFilters) -> bool:
    if filters.source_type is not None and event.source_type != filters.source_type:
        return False
    if filters.event_type is not None and event.event_type != filters.event_type:
        return False
    return within(event.occurred_at, filters)


def within(value: datetime, filters: TimelineFilters) -> bool:
    occurred_at = aware_datetime(value)
    if filters.since is not None and occurred_at < aware_datetime(filters.since):
        return False
    return not (filters.until is not None and occurred_at > aware_datetime(filters.until))


def counts(values: Iterable[str]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for value in values:
        totals[value] = totals.get(value, 0) + 1
    return totals


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def datetime_from_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return aware_datetime(value)
    if isinstance(value, str):
        try:
            return aware_datetime(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None
