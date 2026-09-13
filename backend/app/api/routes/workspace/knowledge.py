from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.dependencies import get_worker_queue
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.workspace.knowledge import (
    KnowledgeCitationResponse,
    KnowledgeSourceCreateRequest,
    KnowledgeSourceIngestionResponse,
    KnowledgeSourceResponse,
    KnowledgeSourceRevisionResponse,
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
from backend.app.domains.knowledge.ingestion import (
    KnowledgeSourceIngestionService,
    enqueue_knowledge_source_ingestion_job,
)
from backend.app.domains.knowledge.service import (
    KnowledgeSourceService,
    KnowledgeSourceVersionConflictError,
)
from backend.app.runtime.workers.queue import RedisQueue

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


@router.post(
    "/{source_id}/ingest",
    response_model=KnowledgeSourceIngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_source(
    source_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> KnowledgeSourceIngestionResponse:
    service = KnowledgeSourceIngestionService(session)
    try:
        ingestion = service.request(
            workspace_id=context.workspace.id,
            source_id=source_id,
            requested_by_user_id=context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    session.commit()
    session.refresh(ingestion)
    enqueue_knowledge_source_ingestion_job(
        queue=queue,
        workspace_id=context.workspace.id,
        source_id=source_id,
        source_version=ingestion.source_version,
        requested_by_user_id=context.user.user_id,
    )
    return KnowledgeSourceIngestionResponse.model_validate(ingestion)


@router.get(
    "/{source_id}/ingestions",
    response_model=PageResponse[KnowledgeSourceIngestionResponse],
)
async def list_ingestions(
    source_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[KnowledgeSourceIngestionResponse]:
    items, total = KnowledgeSourceIngestionService(session).list_ingestions(
        workspace_id=context.workspace.id,
        source_id=source_id,
        limit=page.limit,
        offset=page.offset,
    )
    return PageResponse(
        items=[KnowledgeSourceIngestionResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{source_id}/ingestions/{ingestion_id}",
    response_model=KnowledgeSourceIngestionResponse,
)
async def get_ingestion(
    source_id: UUID,
    ingestion_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> KnowledgeSourceIngestionResponse:
    ingestion = KnowledgeSourceIngestionService(session).get_ingestion(
        workspace_id=context.workspace.id,
        source_id=source_id,
        ingestion_id=ingestion_id,
    )
    if ingestion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge source ingestion not found",
        )
    return KnowledgeSourceIngestionResponse.model_validate(ingestion)


@router.get(
    "/{source_id}/ingestions/{ingestion_id}/citations",
    response_model=PageResponse[KnowledgeCitationResponse],
)
async def list_citations(
    source_id: UUID,
    ingestion_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[KnowledgeCitationResponse]:
    result = KnowledgeSourceIngestionService(session).list_citations(
        workspace_id=context.workspace.id,
        source_id=source_id,
        ingestion_id=ingestion_id,
        limit=page.limit,
        offset=page.offset,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge source ingestion not found",
        )
    items, total = result
    return PageResponse(
        items=[KnowledgeCitationResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{source_id}/revisions",
    response_model=PageResponse[KnowledgeSourceRevisionResponse],
)
async def list_source_revisions(
    source_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[KnowledgeSourceRevisionResponse]:
    items, total = KnowledgeSourceService(session).list_revisions(
        workspace_id=context.workspace.id,
        source_id=source_id,
        limit=page.limit,
        offset=page.offset,
    )
    return PageResponse(
        items=[KnowledgeSourceRevisionResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{source_id}/revisions/{version}",
    response_model=KnowledgeSourceRevisionResponse,
)
async def get_source_revision(
    source_id: UUID,
    version: int,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> KnowledgeSourceRevisionResponse:
    if version < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Knowledge source revision version must be positive",
        )
    revision = KnowledgeSourceService(session).get_revision(
        workspace_id=context.workspace.id,
        source_id=source_id,
        version=version,
    )
    if revision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge source revision not found",
        )
    return KnowledgeSourceRevisionResponse.model_validate(revision)


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
