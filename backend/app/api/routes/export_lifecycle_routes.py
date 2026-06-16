from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceDataLifecycleResponse,
    WorkspaceRecoveryReadinessActionRequest,
    WorkspaceRecoveryReadinessActionResponse,
    WorkspaceRecoveryReadinessResponse,
    WorkspaceRetentionRequest,
    WorkspaceRetentionResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.files.storage import create_storage
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.data_lifecycle import WorkspaceDataLifecycleService

router = APIRouter()


@router.get("/lifecycle-diagnostics", response_model=WorkspaceDataLifecycleResponse)
async def get_workspace_data_lifecycle_diagnostics(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceDataLifecycleResponse:
    diagnostics = WorkspaceDataLifecycleService(session).get_diagnostics(
        workspace_id=context.workspace.id
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceDataLifecycleResponse.model_validate(diagnostics)


@router.get("/recovery-readiness", response_model=WorkspaceRecoveryReadinessResponse)
async def get_workspace_recovery_readiness(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceRecoveryReadinessResponse:
    diagnostics = WorkspaceDataLifecycleService(session).get_recovery_readiness(
        workspace_id=context.workspace.id
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceRecoveryReadinessResponse.model_validate(diagnostics)


@router.post(
    "/recovery-readiness/actions/apply",
    response_model=WorkspaceRecoveryReadinessActionResponse,
)
async def apply_workspace_recovery_readiness_actions(
    request: WorkspaceRecoveryReadinessActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> WorkspaceRecoveryReadinessActionResponse:
    try:
        response = WorkspaceDataLifecycleService(session).apply_recovery_readiness_actions(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            queue=queue,
            storage=create_storage(settings),
            dry_run=request.dry_run,
            actions=request.actions,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceRecoveryReadinessActionResponse.model_validate(response)


@router.post("/retention/preview", response_model=WorkspaceRetentionResponse)
async def preview_workspace_retention(
    request: WorkspaceRetentionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceRetentionResponse:
    response = WorkspaceDataLifecycleService(session).preview_retention(
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        include_files=request.include_files,
        include_export_jobs=request.include_export_jobs,
        include_artifacts=request.include_artifacts,
        max_items=request.max_items,
        require_successful_backup=request.require_successful_backup,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceRetentionResponse.model_validate(response)


@router.post("/retention/apply", response_model=WorkspaceRetentionResponse)
async def apply_workspace_retention(
    request: WorkspaceRetentionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceRetentionResponse:
    response = WorkspaceDataLifecycleService(session).apply_retention(
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        include_files=request.include_files,
        include_export_jobs=request.include_export_jobs,
        include_artifacts=request.include_artifacts,
        max_items=request.max_items,
        require_successful_backup=request.require_successful_backup,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceRetentionResponse.model_validate(response)
