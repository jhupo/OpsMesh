from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter

from .models import TaskSchedule


def next_time(schedule: TaskSchedule, after: datetime) -> datetime | None:
    if schedule.kind == "once":
        return None
    if schedule.kind == "interval":
        if schedule.interval_seconds is None:
            raise ValueError("Interval is required")
        steps = max(
            1, int((after - schedule.next_at).total_seconds() // schedule.interval_seconds) + 1
        )
        return schedule.next_at + timedelta(seconds=steps * schedule.interval_seconds)
    if schedule.kind == "cron" and schedule.expression:
        local = after.astimezone(ZoneInfo(schedule.timezone))
        return croniter(schedule.expression, local).get_next(datetime).astimezone(UTC)
    raise ValueError("Invalid schedule")
