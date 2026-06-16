from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ScheduledJobMaintenanceSummary:
    enqueued: int = 0
    recorded: int = 0
    skipped: int = 0
    enqueued_by_job_type: dict[str, int] | None = None
    recorded_by_job_type: dict[str, int] | None = None
    skipped_by_job_type: dict[str, int] | None = None


@dataclass(frozen=True)
class ScheduledJobCreate:
    name: str
    schedule_type: str
    schedule_config: dict[str, object]
    action_type: str
    job_type: str | None
    resource_id: UUID | None
    routing: dict[str, object]
    priority: int
    max_attempts: int
    metadata: dict[str, object]
