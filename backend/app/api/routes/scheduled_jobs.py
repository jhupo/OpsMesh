from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.scheduled_jobs import (
    ScheduledJobCreateRequest,
    ScheduledJobResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session
from backend.app.scheduled_jobs.service import (
    ScheduledJobCreate,
    WorkspaceScheduledJobService,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/scheduled-jobs",
    tags=["scheduled-jobs"],
)


@router.post("", response_model=ScheduledJobResponse, status_code=status.HTTP_201_CREATED)
async def create_scheduled_job(
    request: ScheduledJobCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> ScheduledJobResponse:
    try:
        scheduled_job = WorkspaceScheduledJobService(session).create(
            workspace=context.workspace,
            user_id=context.user.user_id,
            data=ScheduledJobCreate(
                name=request.name,
                schedule_type=request.schedule.type,
                schedule_config=request.schedule.as_config(),
                action_type=request.action_type,
                job_type=request.job_type,
                resource_id=request.resource_id,
                routing=request.routing,
                priority=request.priority,
                max_attempts=request.max_attempts,
                metadata=request.metadata,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ScheduledJobResponse.model_validate(scheduled_job)


@router.get("", response_model=PageResponse[ScheduledJobResponse])
async def list_scheduled_jobs(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[ScheduledJobResponse]:
    items, total = WorkspaceScheduledJobService(session).list_jobs(
        workspace_id=context.workspace.id,
        limit=page.limit,
        offset=page.offset,
        status=status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/{scheduled_job_id}/pause", response_model=ScheduledJobResponse)
async def pause_scheduled_job(
    scheduled_job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> ScheduledJobResponse:
    try:
        scheduled_job = WorkspaceScheduledJobService(session).pause(
            workspace_id=context.workspace.id,
            scheduled_job_id=scheduled_job_id,
            user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ScheduledJobResponse.model_validate(scheduled_job)


@router.post("/{scheduled_job_id}/resume", response_model=ScheduledJobResponse)
async def resume_scheduled_job(
    scheduled_job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> ScheduledJobResponse:
    try:
        scheduled_job = WorkspaceScheduledJobService(session).resume(
            workspace_id=context.workspace.id,
            scheduled_job_id=scheduled_job_id,
            user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ScheduledJobResponse.model_validate(scheduled_job)
