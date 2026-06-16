from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.app.core.typing import dict_or_empty


@dataclass(frozen=True)
class WorkspaceHealthSnapshotPolicy:
    interval: timedelta


def health_snapshot_policy(settings: object) -> WorkspaceHealthSnapshotPolicy | None:
    root = dict_or_empty(settings)
    operations = dict_or_empty(root.get("operations"))
    policy = dict_or_empty(operations.get("health_snapshots"))
    if policy.get("enabled") is not True:
        return None
    interval = positive_timedelta(policy.get("interval_minutes"), unit="minutes")
    if interval is None:
        interval = positive_timedelta(policy.get("interval_hours"), unit="hours")
    return WorkspaceHealthSnapshotPolicy(interval=interval or timedelta(hours=24))


def positive_timedelta(value: object, *, unit: str) -> timedelta | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value > 0:
        return timedelta(**{unit: float(value)})
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        if parsed > 0:
            return timedelta(**{unit: parsed})
    return None


def snapshot_due(
    latest_created_at: datetime,
    now: datetime,
    interval: timedelta,
) -> bool:
    return aware_datetime(latest_created_at) <= now - interval


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
