from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.workspace.knowledge import (
    KnowledgeSourceCreateRequest,
    KnowledgeSourceResponse,
    KnowledgeSourceStatusRequest,
    KnowledgeSourceUpdateRequest,
)
from backend.app.core.auth.context import WorkspaceContext
from backend.app.core.auth.dependencies import workspace_dependency
from backend.app.core.auth.permissions import WorkspaceAction
from backend.app.core.common.pagination import PageParams
from backend.app.core.db.errors import DatabaseConflictError
from backend.app.core.db.session import get_db_session
from backend.app.domains.knowledge.contracts import (
    KnowledgeSourceCreate,
    KnowledgeSourceStatus,
    KnowledgeSourceUpdate,
)
from backend.app.domains.knowledge.service import (
    KnowledgeSourceService,
    KnowledgeSourceVersionConflictError,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/knowledge/sources", tags=["knowledge"])


@router.get("", response_model=PageResponse[KnowledgeSourceResponse])
async def list_sources(
    page: PageParams = Depends(pagination_params),
    include_archived: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[KnowledgeSourceResponse]:
    items, total = KnowledgeSourceService(session).list_sources(
        context.workspace.id,
        limit=page.limit,
        offset=page.offset,
        include_archived=include_archived,
    )
    return PageResponse(
        items=[KnowledgeSourceResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", response_model=KnowledgeSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    request: KnowledgeSourceCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> KnowledgeSourceResponse:
    try:
        source = KnowledgeSourceService(session).create(
            workspace_id=context.workspace.id,
            actor_user_id=context.user.user_id,
            command=KnowledgeSourceCreate(**request.model_dump()),
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return KnowledgeSourceResponse.model_validate(source)


@router.get("/{source_id}", response_model=KnowledgeSourceResponse)
async def get_source(
    source_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> KnowledgeSourceResponse:
    source = KnowledgeSourceService(session).get_source(context.workspace.id, source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge source not found",
        )
    return KnowledgeSourceResponse.model_validate(source)


@router.patch("/{source_id}", response_model=KnowledgeSourceResponse)
async def update_source(
    source_id: UUID,
    request: KnowledgeSourceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> KnowledgeSourceResponse:
    try:
        source = KnowledgeSourceService(session).update(
            workspace_id=context.workspace.id,
            source_id=source_id,
            actor_user_id=context.user.user_id,
            command=KnowledgeSourceUpdate(**request.model_dump()),
        )
    except KnowledgeSourceVersionConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge source not found",
        )
    return KnowledgeSourceResponse.model_validate(source)


@router.post("/{source_id}/pause", response_model=KnowledgeSourceResponse)
async def pause_source(
    source_id: UUID,
    request: KnowledgeSourceStatusRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> KnowledgeSourceResponse:
    return await _set_status(source_id, request, "paused", context, session)


@router.post("/{source_id}/resume", response_model=KnowledgeSourceResponse)
async def resume_source(
    source_id: UUID,
    request: KnowledgeSourceStatusRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> KnowledgeSourceResponse:
    return await _set_status(source_id, request, "active", context, session)


@router.post("/{source_id}/archive", response_model=KnowledgeSourceResponse)
async def archive_source(
    source_id: UUID,
    request: KnowledgeSourceStatusRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> KnowledgeSourceResponse:
    return await _set_status(source_id, request, "archived", context, session)


async def _set_status(
    source_id: UUID,
    request: KnowledgeSourceStatusRequest,
    target_status: KnowledgeSourceStatus,
    context: WorkspaceContext,
    session: Session,
) -> KnowledgeSourceResponse:
    try:
        source = KnowledgeSourceService(session).set_status(
            workspace_id=context.workspace.id,
            source_id=source_id,
            actor_user_id=context.user.user_id,
            expected_version=request.expected_version,
            status=target_status,
        )
    except KnowledgeSourceVersionConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge source not found",
        )
    return KnowledgeSourceResponse.model_validate(source)
