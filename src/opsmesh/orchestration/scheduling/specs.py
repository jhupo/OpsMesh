"""Translate product schedule input to the native Temporal calendar contract."""

from datetime import UTC, datetime, time

from temporalio.client import ScheduleCalendarSpec, ScheduleRange, ScheduleSpec


def build_schedule_spec(schedule_type: str, config: dict[str, object]) -> ScheduleSpec:
    if schedule_type == "one_shot":
        value = config.get("run_at")
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError("One-shot schedules require run_at")
        when = _utc_datetime(value)
        return ScheduleSpec(
            calendars=[
                ScheduleCalendarSpec(
                    second=[ScheduleRange(when.second)],
                    minute=[ScheduleRange(when.minute)],
                    hour=[ScheduleRange(when.hour)],
                    day_of_month=[ScheduleRange(when.day)],
                    month=[ScheduleRange(when.month)],
                    year=[ScheduleRange(when.year)],
                )
            ],
            time_zone_name="UTC",
        )
    if schedule_type == "hourly":
        minute = config.get("minute", 0)
        if isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute <= 59:
            raise ValueError("Hourly schedules require minute between 0 and 59")
        return ScheduleSpec(
            calendars=[
                ScheduleCalendarSpec(
                    minute=[ScheduleRange(minute)],
                    hour=[ScheduleRange(0, 23)],
                )
            ],
            time_zone_name="UTC",
        )
    if schedule_type == "daily":
        raw = config.get("time_of_day")
        if not isinstance(raw, str):
            raise ValueError("Daily schedules require time_of_day")
        when_time = time.fromisoformat(raw)
        if when_time.tzinfo is not None:
            raise ValueError("Daily time_of_day uses UTC without an offset")
        return ScheduleSpec(
            calendars=[
                ScheduleCalendarSpec(
                    second=[ScheduleRange(when_time.second)],
                    minute=[ScheduleRange(when_time.minute)],
                    hour=[ScheduleRange(when_time.hour)],
                )
            ],
            time_zone_name="UTC",
        )
    raise ValueError("Schedule type must be one_shot, hourly or daily")


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
