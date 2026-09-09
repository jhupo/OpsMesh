from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.memory import (
    SemanticMemoryArchiveRequest,
    SemanticMemoryListResponse,
    SemanticMemoryResponse,
    SemanticMemoryUpsertRequest,
    SemanticMemoryVersionResponse,
    SemanticScope,
)
from backend.app.audit.service import AuditService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session
from backend.app.memory.semantic import (
    AgentSemanticMemoryService,
    SemanticMemoryConflictError,
    SemanticMemoryUpsert,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/memories", tags=["workspace-memory"])


@router.post("/semantic", response_model=SemanticMemoryResponse)
async def upsert_semantic_memory(
    request: SemanticMemoryUpsertRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> SemanticMemoryResponse:
    service = AgentSemanticMemoryService(session)
    try:
        entry = service.upsert(
            SemanticMemoryUpsert(
                workspace_id=context.workspace.id,
                scope_type=request.scope_type,
                scope_id=_scope_id(request, context.workspace.id),
                memory_key=request.memory_key,
                knowledge_type=request.knowledge_type,
                title=request.title,
                content=request.content,
                tags=request.tags,
                importance=request.importance,
                metadata=request.metadata,
                expected_revision=request.expected_revision,
                changed_by_user_id=context.user.user_id,
                change_reason=request.change_reason,
            )
        )
    except SemanticMemoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    AuditService(session).record_user_action(
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        action="semantic_memory.upserted",
        target_type="workspace_memory_entry",
        target_id=entry.id,
        metadata={
            "scope_type": entry.scope_type,
            "scope_id": entry.scope_id,
            "memory_key": entry.memory_key,
            "revision": entry.revision,
        },
    )
    session.commit()
    session.refresh(entry)
    return SemanticMemoryResponse.model_validate(entry)


@router.get("/semantic", response_model=SemanticMemoryListResponse)
async def list_semantic_memory(
    scope_type: SemanticScope | None = Query(default=None),
    scope_id: UUID | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> SemanticMemoryListResponse:
    entries, total = AgentSemanticMemoryService(session).list(
        workspace_id=context.workspace.id,
        scope_type=scope_type,
        scope_id=scope_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return SemanticMemoryListResponse(
        items=[SemanticMemoryResponse.model_validate(entry) for entry in entries],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/semantic/{memory_entry_id}/versions",
    response_model=list[SemanticMemoryVersionResponse],
)
async def list_semantic_memory_versions(
    memory_entry_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> list[SemanticMemoryVersionResponse]:
    service = AgentSemanticMemoryService(session)
    if service.get(
        workspace_id=context.workspace.id,
        memory_entry_id=memory_entry_id,
    ) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Semantic memory not found",
        )
    return [
        SemanticMemoryVersionResponse.model_validate(version)
        for version in service.history(
            workspace_id=context.workspace.id,
            memory_entry_id=memory_entry_id,
        )
    ]


@router.post(
    "/semantic/{memory_entry_id}/archive",
    response_model=SemanticMemoryResponse,
)
async def archive_semantic_memory(
    memory_entry_id: UUID,
    request: SemanticMemoryArchiveRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> SemanticMemoryResponse:
    service = AgentSemanticMemoryService(session)
    entry = service.get(
        workspace_id=context.workspace.id,
        memory_entry_id=memory_entry_id,
    )
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Semantic memory not found",
        )
    try:
        service.archive(
            entry,
            expected_revision=request.expected_revision,
            changed_by_user_id=context.user.user_id,
            change_reason=request.change_reason,
        )
    except SemanticMemoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    AuditService(session).record_user_action(
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        action="semantic_memory.archived",
        target_type="workspace_memory_entry",
        target_id=entry.id,
        metadata={"revision": entry.revision},
    )
    session.commit()
    session.refresh(entry)
    return SemanticMemoryResponse.model_validate(entry)


def _scope_id(request: SemanticMemoryUpsertRequest, workspace_id: UUID) -> UUID:
    if request.scope_type == "workspace":
        if request.scope_id is not None and request.scope_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace memory scope must reference the current workspace",
            )
        return workspace_id
    if request.scope_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"scope_id is required for {request.scope_type} semantic memory",
        )
    return request.scope_id
