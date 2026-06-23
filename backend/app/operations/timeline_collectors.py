from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.operations.timeline_event_collectors import TeamRuntimeEventTimelineCollector
from backend.app.operations.timeline_mcp_collectors import TeamRuntimeMcpTimelineCollector
from backend.app.operations.timeline_message_collectors import (
    TeamRuntimeAuditTimelineCollector,
    TeamRuntimeMessageTimelineCollector,
)
from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters
from backend.app.operations.timeline_queue_collectors import TeamRuntimeQueueTimelineCollector
from backend.app.operations.timeline_scheduler_collectors import (
    TeamRuntimeSchedulerTimelineCollector,
)
from backend.app.operations.timeline_team_context import TeamRuntimeTimelineContext
from backend.app.operations.timeline_worker_collectors import TeamRuntimeWorkerTimelineCollector
from backend.app.workers.queue.redis_queue import RedisQueue


class TeamRuntimeTimelineCollector:
    def __init__(self, session: Session) -> None:
        self._context = TeamRuntimeTimelineContext(session)
        self._messages = TeamRuntimeMessageTimelineCollector(session)
        self._audit = TeamRuntimeAuditTimelineCollector(session, self._context)
        self._scheduler = TeamRuntimeSchedulerTimelineCollector(session)
        self._mcp = TeamRuntimeMcpTimelineCollector(session, self._context)
        self._worker = TeamRuntimeWorkerTimelineCollector(session)
        self._events = TeamRuntimeEventTimelineCollector(session, self._context)
        self._queue = TeamRuntimeQueueTimelineCollector()

    def team_exists(self, workspace_id: UUID, team_id: UUID) -> bool:
        return self._context.team_exists(workspace_id, team_id)

    def collect(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
        queue: RedisQueue | None,
    ) -> list[TimelineEvent]:
        runtime_ids = self._context.team_runtime_ids(workspace_id, team_id)
        events = [
            *self._messages.agent_messages(workspace_id, team_id, filters),
            *self._audit.audit_events(workspace_id, team_id, filters),
            *self._scheduler.scheduler_scan(workspace_id, team_id, filters),
            *self._scheduler.blocked_steps(workspace_id, team_id, filters),
            *self._mcp.mcp_governance_events(workspace_id, team_id, filters),
            *self._worker.worker_leases(workspace_id, team_id, filters),
            *self._events.runtime_events(workspace_id, runtime_ids, filters),
        ]
        if filters.include_runs:
            events.extend(self._events.run_events(workspace_id, team_id, filters))
        if filters.include_queue:
            events.extend(self._queue.queue_events(workspace_id, team_id, filters, queue))
        return events
