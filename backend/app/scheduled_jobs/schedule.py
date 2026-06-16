from __future__ import annotations

from datetime import UTC, datetime, time, timedelta


def next_run_at(
    *,
    schedule_type: str,
    schedule_config: dict[str, object],
    after: datetime,
    include_now: bool,
) -> datetime:
    current_time = utc_datetime(after)
    if schedule_type == "one_shot":
        run_at = datetime_config(schedule_config, "run_at")
        if run_at is None:
            raise ValueError("One-shot scheduled jobs require schedule.run_at")
        return run_at
    if schedule_type == "hourly":
        minute = int_config(schedule_config, "minute", default=0, minimum=0, maximum=59)
        candidate = current_time.replace(minute=minute, second=0, microsecond=0)
        if candidate < current_time or (candidate == current_time and not include_now):
            candidate += timedelta(hours=1)
        return candidate
    if schedule_type == "daily":
        daily_time = time_config(schedule_config, "time_of_day")
        candidate = datetime.combine(current_time.date(), daily_time, tzinfo=UTC)
        if candidate < current_time or (candidate == current_time and not include_now):
            candidate += timedelta(days=1)
        return candidate
    raise ValueError("Scheduled job schedule_type must be one_shot, hourly, or daily")


def datetime_config(config: dict[str, object], key: str) -> datetime | None:
    value = config.get(key)
    if isinstance(value, datetime):
        return utc_datetime(value)
    if isinstance(value, str):
        return utc_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
    return None


def time_config(config: dict[str, object], key: str) -> time:
    value = config.get(key)
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Daily scheduled jobs require schedule.time_of_day as HH:MM") from exc
        return parsed.replace(tzinfo=None)
    raise ValueError("Daily scheduled jobs require schedule.time_of_day")


def int_config(
    config: dict[str, object],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Scheduled job {key} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"Scheduled job {key} must be between {minimum} and {maximum}")
    return value


def utc_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
