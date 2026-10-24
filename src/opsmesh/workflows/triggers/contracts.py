"""Trigger configuration independent of persistence and runtime adapters."""

from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class TriggerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["schedule", "webhook"]
    target_kind: Literal["agent", "workflow"]
    target_id: UUID
    input: JsonValue = Field(default_factory=dict)
    cron: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str = Field(default="UTC", max_length=100)
    enabled: bool = True

    @model_validator(mode="after")
    def schedule_fields(self) -> "TriggerSpec":
        if (self.kind == "schedule") != (self.cron is not None):
            raise ValueError("Only schedules require a cron expression")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("The schedule timezone is unknown") from exc
        return self
