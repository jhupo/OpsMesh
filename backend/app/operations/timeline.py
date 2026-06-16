from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    TeamRuntimeTimelineEventResponse,
    TeamRuntimeTimelineResponse,
    TeamRuntimeTimelineSummaryResponse,
)
from backend.app.operations.timeline_collectors import TeamRuntimeTimelineCollector
from backend.app.operations.timeline_models import TimelineFilters
from backend.app.operations.timeline_utils import (
    counts,
    matches_filters,
    redact_metadata,
    redact_secret_like_text,
)
from backend.app.workers.queue.redis_queue import RedisQueue


class TeamRuntimeTimelineService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def timeline(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
        queue: RedisQueue | None = None,
    ) -> TeamRuntimeTimelineResponse | None:
        collector = TeamRuntimeTimelineCollector(self._session)
        if not collector.team_exists(workspace_id, team_id):
            return None
        events = collector.collect(
            workspace_id=workspace_id,
            team_id=team_id,
            filters=filters,
            queue=queue,
        )
        events = [event for event in events if matches_filters(event, filters)]
        events.sort(key=lambda event: (event.occurred_at, event.id), reverse=True)
        total = len(events)
        page = events[filters.offset : filters.offset + filters.limit]
        return TeamRuntimeTimelineResponse(
            workspace_id=workspace_id,
            team_id=team_id,
            generated_at=datetime.now(UTC),
            limit=filters.limit,
            offset=filters.offset,
            summary=TeamRuntimeTimelineSummaryResponse(
                total_events=total,
                returned_events=len(page),
                source_counts=counts(event.source_type for event in events),
                event_type_counts=counts(event.event_type for event in events),
                include_runs=filters.include_runs,
                include_queue=filters.include_queue,
            ),
            items=[
                TeamRuntimeTimelineEventResponse(
                    id=event.id,
                    source_type=event.source_type,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    resource_id=event.resource_id,
                    message=redact_secret_like_text(event.message),
                    metadata=redact_metadata(event.metadata),
                )
                for event in page
            ],
        )
