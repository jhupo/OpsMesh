from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.agent_runtime.session_management import (
    PersistentAgentSessionManagementService,
)
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.routes.workspace_team_common import (
    _require_team,
    _require_team_session,
    _set_team_session_status_response,
)
from backend.app.api.schemas.agents import (
    AgentSessionClearResponse,
    AgentSessionDetailResponse,
    AgentSessionSummaryResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])

@router.get(
    "/teams/{team_id}/sessions",
    response_model=PageResponse[AgentSessionSummaryResponse],
)
async def list_team_sessions(
    team_id: UUID,
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentSessionSummaryResponse]:
    _require_team(session, context.workspace.id, team_id)
    service = PersistentAgentSessionManagementService(session)
    try:
        total = service.count_sessions(
            workspace_id=context.workspace.id,
            agent_team_id=team_id,
            status=status_filter,
        )
        items = service.list_sessions(
            workspace_id=context.workspace.id,
            agent_team_id=team_id,
            status=status_filter,
            limit=page.limit,
            offset=page.offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/teams/{team_id}/sessions/{session_id}",
    response_model=AgentSessionDetailResponse,
)
async def get_team_session(
    team_id: UUID,
    session_id: UUID,
    item_limit: int = Query(default=50, ge=1, le=500),
    item_offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentSessionDetailResponse:
    _require_team(session, context.workspace.id, team_id)
    detail = PersistentAgentSessionManagementService(session).get_session(
        workspace_id=context.workspace.id,
        session_id=session_id,
        item_limit=item_limit,
        item_offset=item_offset,
    )
    if detail is None or detail.session.agent_team_id != team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team session not found")
    return AgentSessionDetailResponse.model_validate(detail)


@router.post(
    "/teams/{team_id}/sessions/{session_id}/archive",
    response_model=AgentSessionSummaryResponse,
)
async def archive_team_session(
    team_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_team_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        team_id=team_id,
        session_id=session_id,
        action="archive",
    )


@router.post(
    "/teams/{team_id}/sessions/{session_id}/freeze",
    response_model=AgentSessionSummaryResponse,
)
async def freeze_team_session(
    team_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_team_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        team_id=team_id,
        session_id=session_id,
        action="freeze",
    )


@router.post(
    "/teams/{team_id}/sessions/{session_id}/activate",
    response_model=AgentSessionSummaryResponse,
)
async def activate_team_session(
    team_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_team_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        team_id=team_id,
        session_id=session_id,
        action="activate",
    )


@router.delete(
    "/teams/{team_id}/sessions/{session_id}/items",
    response_model=AgentSessionClearResponse,
)
async def clear_team_session_items(
    team_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionClearResponse:
    _require_team_session(session, context.workspace.id, team_id, session_id)
    deleted = PersistentAgentSessionManagementService(session).clear_session_items(
        workspace_id=context.workspace.id,
        session_id=session_id,
    )
    session.commit()
    return AgentSessionClearResponse(deleted_item_count=deleted)

