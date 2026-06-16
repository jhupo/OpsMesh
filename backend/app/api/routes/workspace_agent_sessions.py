from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.agent_runtime.session_management import (
    PersistentAgentSessionManagementService,
)
from backend.app.agents.service import AgentManagementService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.agents import (
    AgentSessionClearResponse,
    AgentSessionCompactionResponse,
    AgentSessionCompactRequest,
    AgentSessionDetailResponse,
    AgentSessionSummaryResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session

router = APIRouter(
    prefix="/workspaces/{workspace_id}/agents/{agent_id}",
    tags=["workspace-resources"],
)


@router.get("/sessions", response_model=PageResponse[AgentSessionSummaryResponse])
async def list_agent_sessions(
    agent_id: UUID,
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    team_id: UUID | None = Query(default=None),
    task_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentSessionSummaryResponse]:
    if AgentManagementService(session).get_agent(context.workspace.id, agent_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    service = PersistentAgentSessionManagementService(session)
    try:
        total = service.count_sessions(
            workspace_id=context.workspace.id,
            agent_profile_id=agent_id,
            agent_team_id=team_id,
            task_id=task_id,
            status=status_filter,
        )
        items = service.list_sessions(
            workspace_id=context.workspace.id,
            agent_profile_id=agent_id,
            agent_team_id=team_id,
            task_id=task_id,
            status=status_filter,
            limit=page.limit,
            offset=page.offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/sessions/{session_id}", response_model=AgentSessionDetailResponse)
async def get_agent_session(
    agent_id: UUID,
    session_id: UUID,
    item_limit: int = Query(default=50, ge=1, le=500),
    item_offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentSessionDetailResponse:
    if AgentManagementService(session).get_agent(context.workspace.id, agent_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    detail = PersistentAgentSessionManagementService(session).get_session(
        workspace_id=context.workspace.id,
        session_id=session_id,
        item_limit=item_limit,
        item_offset=item_offset,
    )
    if detail is None or detail.session.agent_profile_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")
    return AgentSessionDetailResponse.model_validate(detail)


@router.post("/sessions/{session_id}/archive", response_model=AgentSessionSummaryResponse)
async def archive_agent_session(
    agent_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_agent_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        agent_id=agent_id,
        session_id=session_id,
        action="archive",
    )


@router.post("/sessions/{session_id}/freeze", response_model=AgentSessionSummaryResponse)
async def freeze_agent_session(
    agent_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_agent_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        agent_id=agent_id,
        session_id=session_id,
        action="freeze",
    )


@router.post("/sessions/{session_id}/activate", response_model=AgentSessionSummaryResponse)
async def activate_agent_session(
    agent_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionSummaryResponse:
    return _set_agent_session_status_response(
        db_session=session,
        workspace_id=context.workspace.id,
        agent_id=agent_id,
        session_id=session_id,
        action="activate",
    )


@router.delete("/sessions/{session_id}/items", response_model=AgentSessionClearResponse)
async def clear_agent_session_items(
    agent_id: UUID,
    session_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionClearResponse:
    _require_agent_session(session, context.workspace.id, agent_id, session_id)
    deleted = PersistentAgentSessionManagementService(session).clear_session_items(
        workspace_id=context.workspace.id,
        session_id=session_id,
    )
    session.commit()
    return AgentSessionClearResponse(deleted_item_count=deleted)


@router.post("/sessions/{session_id}/compact", response_model=AgentSessionCompactionResponse)
async def compact_agent_session(
    agent_id: UUID,
    session_id: UUID,
    request: AgentSessionCompactRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentSessionCompactionResponse:
    _require_agent_session(session, context.workspace.id, agent_id, session_id)
    try:
        result = PersistentAgentSessionManagementService(session).compact_session(
            workspace_id=context.workspace.id,
            session_id=session_id,
            fold_first_n=request.fold_first_n,
            keep_recent_m=request.keep_recent_m,
            summary_role=request.summary_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")
    session.commit()
    return AgentSessionCompactionResponse.model_validate(result)


def _require_agent_session(
    db_session: Session,
    workspace_id: UUID,
    agent_id: UUID,
    session_id: UUID,
) -> None:
    if AgentManagementService(db_session).get_agent(workspace_id, agent_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    detail = PersistentAgentSessionManagementService(db_session).get_session(
        workspace_id=workspace_id,
        session_id=session_id,
        item_limit=1,
    )
    if detail is None or detail.session.agent_profile_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")


def _set_agent_session_status_response(
    *,
    db_session: Session,
    workspace_id: UUID,
    agent_id: UUID,
    session_id: UUID,
    action: str,
) -> AgentSessionSummaryResponse:
    _require_agent_session(db_session, workspace_id, agent_id, session_id)
    service = PersistentAgentSessionManagementService(db_session)
    if action == "archive":
        summary = service.archive_session(workspace_id=workspace_id, session_id=session_id)
    elif action == "freeze":
        summary = service.freeze_session(workspace_id=workspace_id, session_id=session_id)
    elif action == "activate":
        summary = service.activate_session(workspace_id=workspace_id, session_id=session_id)
    else:
        raise ValueError(f"Unsupported session action: {action}")
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")
    db_session.commit()
    return AgentSessionSummaryResponse.model_validate(summary)
