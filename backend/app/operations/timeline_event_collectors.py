from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters
from backend.app.operations.timeline_team_context import TeamRuntimeTimelineContext
from backend.app.operations.timeline_utils import apply_time_filters, aware_datetime
from backend.app.runs.models import RunEvent
from backend.app.runtimes.models import RuntimeEvent


class TeamRuntimeEventTimelineCollector:
    def __init__(self, session: Session, context: TeamRuntimeTimelineContext) -> None:
        self._session = session
        self._context = context

    def runtime_events(
        self,
        workspace_id: UUID,
        runtime_ids: set[UUID],
        filters: TimelineFilters,
    ) -> list[TimelineEvent]:
        if not runtime_ids:
            return []
        statement = select(RuntimeEvent).where(
            RuntimeEvent.workspace_id == workspace_id,
            RuntimeEvent.workspace_runtime_id.in_(runtime_ids),
        )
        statement = apply_time_filters(statement, RuntimeEvent.created_at, filters)
        runtime_events = self._session.scalars(statement).all()
        return [
            TimelineEvent(
                id=f"runtime_event:{event.id}",
                source_type="runtime_event",
                event_type=event.event_type,
                occurred_at=aware_datetime(event.created_at),
                resource_id=str(event.id),
                message=event.message,
                metadata={
                    "runtime_event_id": str(event.id),
                    "workspace_runtime_id": str(event.workspace_runtime_id),
                    "runtime_space_id": str(event.runtime_space_id)
                    if event.runtime_space_id is not None
                    else None,
                    "event_metadata": dict(event.event_metadata or {}),
                },
            )
            for event in runtime_events
        ]

    def run_events(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[TimelineEvent]:
        run_ids = self._context.team_run_event_ids(workspace_id, team_id)
        if not run_ids:
            return []
        events_statement = select(RunEvent).where(
            RunEvent.workspace_id == workspace_id,
            RunEvent.agent_run_id.in_(run_ids),
        )
        events_statement = apply_time_filters(events_statement, RunEvent.created_at, filters)
        run_events = self._session.scalars(events_statement).all()
        return [
            TimelineEvent(
                id=f"run_event:{event.id}",
                source_type="run_event",
                event_type=event.event_type,
                occurred_at=aware_datetime(event.created_at),
                resource_id=str(event.id),
                message=event.message,
                metadata={
                    "run_event_id": str(event.id),
                    "agent_run_id": str(event.agent_run_id),
                    "sequence": event.sequence,
                    "event_metadata": dict(event.event_metadata or {}),
                },
            )
            for event in run_events
        ]
