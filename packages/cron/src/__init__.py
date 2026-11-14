"""PostgreSQL schedule intent with native APScheduler framework events."""

# ruff: noqa: E402

from pathlib import Path

from opsmesh.plugins import PluginMetadata, require

database = require("database")

from .config import Settings
from .declarations import on_cron, on_date, on_interval, on_schedule
from .errors import ScheduleError, ScheduleNotFound, SchedulerUnavailable
from .events import ScheduledEvent
from .operations import (
    add_schedule,
    delete_schedule,
    get_schedule,
    list_schedules,
    pause_schedule,
    reset_schedule,
    resume_schedule,
    update_schedule,
)
from .schemas import (
    CronDefinition,
    DateDefinition,
    IntervalDefinition,
    ScheduleDefinition,
    ScheduleView,
)

__plugin_meta__ = PluginMetadata(
    name="cron",
    description="Persistent schedules dispatched through framework matchers",
    usage="Declare a scheduled Matcher or add a dynamic schedule.",
    type="library",
    config=Settings,
    extra={
        "database": database.DatabaseSchema(
            Path(__file__).parent / "migrations", "0001_cron"
        )
    },
)

__all__ = [
    "Settings",
    "ScheduleError",
    "ScheduleNotFound",
    "SchedulerUnavailable",
    "ScheduledEvent",
    "CronDefinition",
    "DateDefinition",
    "IntervalDefinition",
    "ScheduleDefinition",
    "ScheduleView",
    "on_cron",
    "on_interval",
    "on_date",
    "on_schedule",
    "add_schedule",
    "get_schedule",
    "list_schedules",
    "update_schedule",
    "pause_schedule",
    "resume_schedule",
    "delete_schedule",
    "reset_schedule",
]
