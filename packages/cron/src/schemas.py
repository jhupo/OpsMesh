"""JSON-only schedule definitions backed by native APScheduler triggers."""

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from .config import Settings


class CronDefinition(BaseModel):
    """A five-field cron expression with an explicit IANA timezone."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["cron"] = "cron"
    expression: str
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Reject persisted or supplied unknown timezone names."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    def build(self) -> CronTrigger:
        """Build the native trigger and validate the expression."""
        return CronTrigger.from_crontab(
            self.expression, timezone=ZoneInfo(self.timezone)
        )


class IntervalDefinition(BaseModel):
    """An interval anchored to a persisted, timezone-aware instant."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["interval"] = "interval"
    seconds: float = Field(gt=0, le=31536000)
    start_at: datetime

    @field_validator("start_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous naive anchors."""
        if value.utcoffset() is None:
            raise ValueError("An interval anchor must include a timezone")
        return value

    def build(self) -> IntervalTrigger:
        """Keep the persisted interval anchor across restarts."""
        return IntervalTrigger(seconds=self.seconds, start_date=self.start_at)


class DateDefinition(BaseModel):
    """A single timezone-aware firing instant."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["date"] = "date"
    run_at: datetime

    @field_validator("run_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous naive firing instants."""
        if value.utcoffset() is None:
            raise ValueError("A date trigger must include a timezone")
        return value

    def build(self) -> DateTrigger:
        """Build the native single-fire trigger."""
        return DateTrigger(run_date=self.run_at)


TriggerDefinition = Annotated[
    CronDefinition | IntervalDefinition | DateDefinition,
    Field(discriminator="type"),
]
ScheduleName = Annotated[
    str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
]


class ScheduleDefinition(BaseModel):
    """A persistent definition, without callable or credential references."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: ScheduleName
    handler: ScheduleName
    trigger: TriggerDefinition
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True
    max_instances: int = Field(default=1, ge=1, le=100)
    coalesce: bool = True
    misfire_grace_seconds: int | None = Field(default=None, ge=1, le=86400)
    timeout_seconds: float | None = Field(default=None, gt=0, le=86400)


def resolve_schedule_definition(
    definition: ScheduleDefinition, settings: Settings
) -> ScheduleDefinition:
    """Apply one plugin configuration to omitted plan-level defaults."""
    return definition.model_copy(
        update={
            "timeout_seconds": (
                definition.timeout_seconds
                if definition.timeout_seconds is not None
                else settings.timeout_seconds
            ),
            "misfire_grace_seconds": (
                definition.misfire_grace_seconds
                if definition.misfire_grace_seconds is not None
                else settings.misfire_grace_seconds
            ),
        }
    )


class ScheduleView(BaseModel):
    """A definition and its current process-local availability."""

    id: str
    owner_plugin: str
    name: str
    source: Literal["code", "dynamic"]
    definition: ScheduleDefinition | None
    completed: bool
    expired: bool
    runnable: bool
    blocked_reason: str | None = None
    next_run_at: datetime | None = None
