"""Numeric five-field cron calendars, with explicit IANA gap/fold handling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.scheduling.api import PersistentSchedule


def _field(value: str, minimum: int, maximum: int) -> frozenset[int]:
    result: set[int] = set()
    for part in value.split(","):
        base, slash, step_text = part.partition("/")
        step = int(step_text) if slash else 1
        if step <= 0:
            raise ValueError("Cron steps must be positive")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            first, last = base.split("-", 1)
            start, end = int(first), int(last)
        else:
            start = int(base)
            end = maximum if slash else start
        if not minimum <= start <= end <= maximum:
            raise ValueError("Cron field lies outside its allowed range")
        result.update(range(start, end + 1, step))
    if not result:
        raise ValueError("Cron fields cannot be empty")
    return frozenset(result)


@dataclass(frozen=True)
class CronCalendar:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_wildcard: bool
    weekday_wildcard: bool

    @classmethod
    def parse(cls, expression: str) -> CronCalendar:
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError("Cron requires five numeric fields: minute hour day month weekday")
        minute, hour, day, month, weekday = parts
        return cls(
            _field(minute, 0, 59),
            _field(hour, 0, 23),
            _field(day, 1, 31),
            _field(month, 1, 12),
            frozenset(i % 7 for i in _field(weekday, 0, 7)),
            day.startswith("*"),
            weekday.startswith("*"),
        )

    def matches_day(self, value: date) -> bool:
        if value.month not in self.months:
            return False
        day_matches = value.day in self.days
        weekday_matches = (value.weekday() + 1) % 7 in self.weekdays
        if self.day_wildcard or self.weekday_wildcard:
            return day_matches and weekday_matches
        return day_matches or weekday_matches

    def next_after(self, after: datetime, timezone: str) -> datetime:
        zone = ZoneInfo(timezone)
        local_day = after.astimezone(zone).date()
        for _ in range(366 * 5):
            if self.matches_day(local_day):
                candidates: set[datetime] = set()
                for hour in self.hours:
                    for minute in self.minutes:
                        wall = datetime.combine(local_day, datetime.min.time()).replace(
                            hour=hour, minute=minute
                        )
                        for fold in (0, 1):
                            instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                            returned = instant.astimezone(zone)
                            if returned.replace(tzinfo=None) == wall and instant > after:
                                candidates.add(instant)
                if candidates:
                    return min(candidates)
            local_day += timedelta(days=1)
        raise OpsMeshError("VALIDATION_ERROR", "Cron has no occurrence within five years")


def normalize_schedule(schedule: PersistentSchedule) -> PersistentSchedule:
    if schedule.cron_expression is None:
        return schedule
    try:
        calendar = CronCalendar.parse(schedule.cron_expression)
        first = calendar.next_after(
            schedule.next_run_at - timedelta(microseconds=1), schedule.timezone
        )
    except ValueError as exc:
        raise OpsMeshError("VALIDATION_ERROR", f"Cron expression is invalid: {exc}") from exc
    return schedule.model_copy(update={"next_run_at": first})


def next_after(schedule: PersistentSchedule, instant: datetime) -> datetime | None:
    if schedule.cron_expression is not None:
        return CronCalendar.parse(schedule.cron_expression).next_after(instant, schedule.timezone)
    if schedule.interval_seconds is not None:
        elapsed = max(0, (instant - schedule.next_run_at).total_seconds())
        steps = math.floor(elapsed / schedule.interval_seconds) + 1
        return schedule.next_run_at + timedelta(seconds=steps * schedule.interval_seconds)
    return None


def due_times(
    schedule: PersistentSchedule, horizon: datetime
) -> tuple[list[datetime], datetime | None]:
    """Return bounded due work; catch-up leaves the remaining backlog for later ticks."""
    first = schedule.next_run_at
    if first > horizon:
        return [], first
    if schedule.misfire_policy != "catch_up":
        # One occurrence represents the missed range; retain its original trigger ID.
        return [first], next_after(schedule, horizon)
    times: list[datetime] = []
    current: datetime | None = first
    while current is not None and current <= horizon and len(times) < schedule.max_catch_up:
        times.append(current)
        current = next_after(schedule, current)
    return times, current
