from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TimelineFilters:
    limit: int = 50
    offset: int = 0
    source_type: str | None = None
    event_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    include_runs: bool = False
    include_queue: bool = True


@dataclass(frozen=True)
class TimelineEvent:
    id: str
    source_type: str
    event_type: str
    occurred_at: datetime
    resource_id: str
    message: str
    metadata: dict[str, object]
