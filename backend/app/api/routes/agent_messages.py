from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.agent_messages.service import AgentMailboxService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.agent_messages import (
    AgentInboxSummaryResponse,
    AgentMailboxSummaryResponse,
    AgentMessageCreateRequest,
    AgentMessageMarkReadRequest,
    AgentMessageResponse,
    AgentMessageThreadCreateRequest,
    AgentMessageThreadResponse,
    AgentMessageThreadStatusRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session

router = APIRouter(
    prefix="/workspaces/{workspace_id}/agent-message-threads",
    tags=["agent-messages"],
)


@router.get("", response_model=PageResponse[AgentMessageThreadResponse])
async def list_agent_message_threads(
    page: PageParams = Depends(pagination_params),
    task_id: UUID | None = Query(default=None),
    agent_team_id: UUID | None = Query(default=None),
    agent_profile_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentMessageThreadResponse]:
    try:
        items, total = AgentMailboxService(session).list_threads(
            workspace_id=context.workspace.id,
            page=page,
            task_id=task_id,
            agent_team_id=agent_team_id,
            agent_profile_id=agent_profile_id,
            status=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/summary", response_model=AgentMailboxSummaryResponse)
async def get_agent_mailbox_summary(
    latest_limit: int = Query(default=20, ge=0, le=100),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentMailboxSummaryResponse:
    summary = AgentMailboxService(session).get_summary(
        workspace_id=context.workspace.id,
        latest_limit=latest_limit,
    )
    return AgentMailboxSummaryResponse.model_validate(summary)


@router.get("/agents/{agent_profile_id}/inbox", response_model=AgentInboxSummaryResponse)
async def get_agent_inbox(
    agent_profile_id: UUID,
    latest_limit: int = Query(default=20, ge=0, le=100),
    unread_only: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentInboxSummaryResponse:
    try:
        inbox = AgentMailboxService(session).get_agent_inbox(
            workspace_id=context.workspace.id,
            agent_profile_id=agent_profile_id,
            latest_limit=latest_limit,
            unread_only=unread_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AgentInboxSummaryResponse.model_validate(inbox)


@router.post(
    "",
    response_model=AgentMessageThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_message_thread(
    request: AgentMessageThreadCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentMessageThreadResponse:
    try:
        thread = AgentMailboxService(session).create_thread(
            workspace_id=context.workspace.id,
            data=request,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    session.commit()
    return AgentMessageThreadResponse.model_validate(thread)


@router.post("/{thread_id}/status", response_model=AgentMessageThreadResponse)
async def set_agent_message_thread_status(
    thread_id: UUID,
    request: AgentMessageThreadStatusRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentMessageThreadResponse:
    try:
        thread = AgentMailboxService(session).set_thread_status(
            workspace_id=context.workspace.id,
            thread_id=thread_id,
            status=request.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    session.commit()
    return AgentMessageThreadResponse.model_validate(thread)


@router.get("/{thread_id}/messages", response_model=PageResponse[AgentMessageResponse])
async def list_agent_messages(
    thread_id: UUID,
    page: PageParams = Depends(pagination_params),
    recipient_agent_profile_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentMessageResponse]:
    try:
        items, total = AgentMailboxService(session).list_messages(
            workspace_id=context.workspace.id,
            thread_id=thread_id,
            page=page,
            recipient_agent_profile_id=recipient_agent_profile_id,
            status=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/{thread_id}/messages",
    response_model=AgentMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_message(
    thread_id: UUID,
    request: AgentMessageCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentMessageResponse:
    try:
        message = AgentMailboxService(session).create_message(
            workspace_id=context.workspace.id,
            thread_id=thread_id,
            data=request,
        )
    except ValueError as exc:
        error_message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in error_message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=error_message) from exc
    session.commit()
    return AgentMessageResponse.model_validate(message)


@router.post("/messages/{message_id}/read", response_model=AgentMessageResponse)
async def mark_agent_message_read(
    message_id: UUID,
    request: AgentMessageMarkReadRequest | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentMessageResponse:
    message = AgentMailboxService(session).mark_message_read(
        workspace_id=context.workspace.id,
        message_id=message_id,
        read_at=request.read_at if request is not None else None,
    )
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    session.commit()
    return AgentMessageResponse.model_validate(message)
