"""Durable schedules: configuration, occurrence evidence and authorized operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, Self
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from opsmesh.framework.adapters import ReplyTarget
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import ErrorInfo
from opsmesh.framework.events import DispatchContext, OpsMeshEvent
from opsmesh.framework.message import DispatchResult
from opsmesh.framework.typing import JsonData, OpsMeshContract
from opsmesh.modules.runtime.api import AgentRunView, RunOverrides

ScheduleAuthorizer = Callable[[str, str, str], Awaitable[None]]
ScheduleDispatcher = Callable[[DispatchContext[OpsMeshEvent]], Awaitable[DispatchResult]]
ScheduleAgentResolver = Callable[[str], Awaitable[AgentSpecification]]
ScheduleRunResolver = Callable[[str], Awaitable[AgentRunView]]
ScheduleSubmissionResolver = Callable[[str, str], Awaitable[AgentRunView | None]]


class PersistentSchedule(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100)
    revision: int = Field(default=1, ge=1)
    principal_id: str = Field(min_length=1)
    next_run_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    interval_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    cron_expression: str | None = None
    timezone: str = "UTC"
    agent_revision: str | None = None
    payload: JsonData = Field(default_factory=dict)
    input: str | list[dict[str, Any]] | None = None
    run_overrides: RunOverrides = Field(default_factory=RunOverrides)
    session_policy: Literal["per_fire", "shared"] = "per_fire"
    session_id: str | None = Field(default=None, min_length=1, max_length=150)
    reply_target: ReplyTarget | None = None
    notify_on_failure: bool = True
    notify_on_approval: bool = True
    misfire_policy: Literal["skip", "coalesce", "catch_up"] = "coalesce"
    misfire_grace_seconds: float = Field(default=30, ge=0, allow_inf_nan=False)
    max_catch_up: int = Field(default=10, ge=1, le=100)
    max_active_runs: int = Field(default=1, ge=1, le=100)
    concurrency_policy: Literal["skip", "queue"] = "queue"
    max_queued_occurrences: int = Field(default=100, ge=1, le=10000)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def portable_identifier(cls, value: str) -> str:
        if value.startswith("_occurrence:") or any(ord(character) < 32 for character in value):
            raise ValueError(
                "Schedule IDs must be printable and must not use the occurrence namespace"
            )
        return value

    @field_validator("next_run_at")
    @classmethod
    def aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Schedule timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def consistent_options(self) -> Self:
        if self.cron_expression and self.interval_seconds is not None:
            raise ValueError("A schedule cannot use interval and cron together")
        if self.session_id and self.session_policy != "shared":
            raise ValueError("An explicit session_id requires shared session policy")
        return self


ScheduleTargetValidator = Callable[[PersistentSchedule, str], Awaitable[None]]
OccurrenceStatus = Literal[
    "queued",
    "dispatching",
    "submitted",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "skipped",
]


class ScheduleOccurrence(OpsMeshContract):
    id: str
    schedule_id: str
    schedule_revision: int
    event_id: str
    scheduled_at: datetime
    status: OccurrenceStatus
    trigger: Literal["scheduled", "manual"] = "scheduled"
    run_id: str | None = None
    reason: str | None = None
    error: ErrorInfo | None = None
    created_at: datetime
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class SchedulePreview(OpsMeshContract):
    schedule_id: str
    revision: int
    timezone: str
    times: list[datetime]
    dst_policy: str = "skip_nonexistent_repeat_ambiguous"


ScheduleOccurrenceObserver = Callable[[ScheduleOccurrence, PersistentSchedule], Awaitable[None]]


class SchedulingService(Protocol):
    """HTTP and plugin callers share these authorization and version checks."""

    async def create(
        self, schedule: PersistentSchedule, *, actor_id: str
    ) -> PersistentSchedule: ...
    async def get(self, schedule_id: str, *, actor_id: str) -> PersistentSchedule: ...
    async def list_schedules(self, *, actor_id: str) -> list[PersistentSchedule]: ...
    async def update(
        self,
        schedule_id: str,
        schedule: PersistentSchedule,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> PersistentSchedule: ...
    async def delete(self, schedule_id: str, expected_revision: int, *, actor_id: str) -> None: ...
    async def set_enabled(
        self,
        schedule_id: str,
        enabled: bool,
        *,
        actor_id: str,
        expected_revision: int | None = None,
    ) -> PersistentSchedule: ...
    async def run_now(
        self,
        schedule_id: str,
        expected_revision: int,
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> ScheduleOccurrence: ...
    async def preview(
        self,
        schedule_id: str,
        expected_revision: int,
        *,
        actor_id: str,
        count: int = 10,
        after: datetime | None = None,
    ) -> SchedulePreview: ...
    async def list_occurrences(
        self,
        schedule_id: str,
        *,
        actor_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScheduleOccurrence]: ...
