from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.runtime.spaces.contracts import (
    RuntimeSpaceControlResponse,
    RuntimeSpaceCreateRequest,
    RuntimeSpaceDiagnosticsResponse,
    RuntimeSpaceEventResponse,
    RuntimeSpaceForceReleaseRequest,
    RuntimeSpaceForceReleaseResponse,
    RuntimeSpacePauseRequest,
    RuntimeSpaceResetResponse,
    RuntimeSpaceResponse,
    RuntimeSpaceUpdateRequest,
)
from opsmesh.runtime.spaces.service import RuntimeSpaceService
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["runtime-spaces"])


@router.get("/runtime-spaces", response_model=PageResponse[RuntimeSpaceResponse])
def list_runtime_spaces(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    scope: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RuntimeSpaceResponse]:
    items, total = RuntimeSpaceService(session).list_runtime_spaces(
        context.workspace.id,
        page,
        status=status_filter,
        scope=scope,
    )
    return PageResponse(
        items=[RuntimeSpaceResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/runtime-spaces",
    response_model=RuntimeSpaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_runtime_space(
    request: RuntimeSpaceCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> RuntimeSpaceResponse:
    try:
        runtime_space = RuntimeSpaceService(session).create_runtime_space(
            workspace_id=context.workspace.id,
            name=request.name,
            scope=request.scope,
            target_id=request.target_id,
            created_by_user_id=context.user.user_id,
            default_runtime_template_id=request.default_runtime_template_id,
            policy=request.policy,
            network_policy=request.network_policy,
            storage_policy=request.storage_policy,
            cleanup_policy=request.cleanup_policy,
            quota_limits=request.quota_limits,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return RuntimeSpaceResponse.model_validate(runtime_space)


@router.get("/runtime-spaces/{runtime_space_id}", response_model=RuntimeSpaceResponse)
def get_runtime_space(
    runtime_space_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> RuntimeSpaceResponse:
    runtime_space = RuntimeSpaceService(session).get_runtime_space(
        context.workspace.id,
        runtime_space_id,
    )
    if runtime_space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime space not found")
    return RuntimeSpaceResponse.model_validate(runtime_space)


@router.get(
    "/runtime-spaces/{runtime_space_id}/diagnostics",
    response_model=RuntimeSpaceDiagnosticsResponse,
)
def get_runtime_space_diagnostics(
    runtime_space_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> RuntimeSpaceDiagnosticsResponse:
    diagnostics = RuntimeSpaceService(session).diagnostics(
        context.workspace.id,
        runtime_space_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime space not found")
    return diagnostics


@router.patch("/runtime-spaces/{runtime_space_id}", response_model=RuntimeSpaceResponse)
def update_runtime_space(
    runtime_space_id: UUID,
    request: RuntimeSpaceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> RuntimeSpaceResponse:
    runtime_space = RuntimeSpaceService(session).update_runtime_space(
        workspace_id=context.workspace.id,
        runtime_space_id=runtime_space_id,
        name=request.name,
        status=request.status,
        policy=request.policy,
        network_policy=request.network_policy,
        storage_policy=request.storage_policy,
        cleanup_policy=request.cleanup_policy,
        quota_limits=request.quota_limits,
    )
    if runtime_space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime space not found")
    return RuntimeSpaceResponse.model_validate(runtime_space)


@router.post("/runtime-spaces/{runtime_space_id}/reset", response_model=RuntimeSpaceResetResponse)
def reset_runtime_space(
    runtime_space_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> RuntimeSpaceResetResponse:
    result = RuntimeSpaceService(session).reset_runtime_space(
        context.workspace.id,
        runtime_space_id,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime space not found")
    runtime_space, released, cleared, affected_runtimes = result
    return RuntimeSpaceResetResponse(
        runtime_space=RuntimeSpaceResponse.model_validate(runtime_space),
        released_reservations=released,
        cleared_blocked_steps=cleared,
        affected_runtimes=affected_runtimes,
    )


@router.post(
    "/runtime-spaces/{runtime_space_id}/pause",
    response_model=RuntimeSpaceControlResponse,
)
def pause_runtime_space(
    runtime_space_id: UUID,
    request: RuntimeSpacePauseRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> RuntimeSpaceControlResponse:
    runtime_space = RuntimeSpaceService(session).pause_runtime_space(
        workspace_id=context.workspace.id,
        runtime_space_id=runtime_space_id,
        reason=request.reason,
    )
    if runtime_space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime space not found")
    return RuntimeSpaceControlResponse(
        runtime_space=RuntimeSpaceResponse.model_validate(runtime_space),
        cleared_blocked_steps=0,
    )


@router.post(
    "/runtime-spaces/{runtime_space_id}/resume",
    response_model=RuntimeSpaceControlResponse,
)
def resume_runtime_space(
    runtime_space_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> RuntimeSpaceControlResponse:
    result = RuntimeSpaceService(session).resume_runtime_space(
        workspace_id=context.workspace.id,
        runtime_space_id=runtime_space_id,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime space not found")
    runtime_space, cleared = result
    return RuntimeSpaceControlResponse(
        runtime_space=RuntimeSpaceResponse.model_validate(runtime_space),
        cleared_blocked_steps=cleared,
    )


@router.post(
    "/runtime-spaces/{runtime_space_id}/reservations/force-release",
    response_model=RuntimeSpaceForceReleaseResponse,
)
def force_release_runtime_space_reservations(
    runtime_space_id: UUID,
    request: RuntimeSpaceForceReleaseRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> RuntimeSpaceForceReleaseResponse:
    result = RuntimeSpaceService(session).force_release_reservations(
        workspace_id=context.workspace.id,
        runtime_space_id=runtime_space_id,
        reservation_key=request.reservation_key,
        reason=request.reason,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime space not found")
    runtime_space, released, cleared = result
    return RuntimeSpaceForceReleaseResponse(
        runtime_space=RuntimeSpaceResponse.model_validate(runtime_space),
        released_reservations=released,
        cleared_blocked_steps=cleared,
    )


@router.get(
    "/runtime-spaces/{runtime_space_id}/events",
    response_model=PageResponse[RuntimeSpaceEventResponse],
)
def list_runtime_space_events(
    runtime_space_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RuntimeSpaceEventResponse]:
    result = RuntimeSpaceService(session).list_events(
        context.workspace.id,
        runtime_space_id,
        page,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime space not found")
    items, total = result
    return PageResponse(
        items=[RuntimeSpaceEventResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
