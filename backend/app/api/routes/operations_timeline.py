from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    TeamRuntimeTimelineResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session
from backend.app.operations.timeline import TeamRuntimeTimelineService
from backend.app.operations.timeline_models import TimelineFilters
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.get(
    "/team-runtimes/{team_id}/timeline",
    response_model=TeamRuntimeTimelineResponse,
)
async def team_runtime_timeline(
    team_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    source_type: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    include_runs: bool = Query(default=False),
    include_queue: bool = Query(default=True),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> TeamRuntimeTimelineResponse:
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=400, detail="since must be before until")
    response = TeamRuntimeTimelineService(session).timeline(
        workspace_id=context.workspace.id,
        team_id=team_id,
        filters=TimelineFilters(
            limit=limit,
            offset=offset,
            source_type=source_type,
            event_type=event_type,
            since=since,
            until=until,
            include_runs=include_runs,
            include_queue=include_queue,
        ),
        queue=queue,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return response
