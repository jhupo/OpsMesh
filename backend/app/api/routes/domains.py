from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.domains import (
    DomainItemCreateRequest,
    DomainItemResponse,
    DomainProjectCreateRequest,
    DomainProjectResponse,
    ReviewCommentCreateRequest,
    ReviewCommentResponse,
    RevisionRequestCreateRequest,
    RevisionRequestResponse,
    TaskViewResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session
from backend.app.domains.service import DomainTaskService
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["domain-tasks"])


@router.get("/domain-projects", response_model=PageResponse[DomainProjectResponse])
async def list_domain_projects(
    page: PageParams = Depends(pagination_params),
    domain_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[DomainProjectResponse]:
    items, total = DomainTaskService(session).list_projects(
        context.workspace.id,
        page,
        domain_type,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/domain-projects",
    response_model=DomainProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_domain_project(
    request: DomainProjectCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> DomainProjectResponse:
    try:
        project = DomainTaskService(session).create_project(context.workspace.id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DomainProjectResponse.model_validate(project)


@router.get("/domain-items", response_model=PageResponse[DomainItemResponse])
async def list_domain_items(
    page: PageParams = Depends(pagination_params),
    task_id: UUID | None = Query(default=None),
    project_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[DomainItemResponse]:
    items, total = DomainTaskService(session).list_items(
        context.workspace.id,
        page,
        task_id,
        project_id,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/domain-items",
    response_model=DomainItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_domain_item(
    request: DomainItemCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> DomainItemResponse:
    try:
        item = DomainTaskService(session).create_item(context.workspace.id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DomainItemResponse.model_validate(item)


@router.get("/tasks/{task_id}/view", response_model=TaskViewResponse)
async def get_task_view(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskViewResponse:
    view = DomainTaskService(session).task_view(context.workspace.id, task_id)
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task, project, items, comments, revision_requests = view
    return TaskViewResponse(
        task=task,
        domain_project=project,
        domain_items=items,
        review_comments=comments,
        revision_requests=revision_requests,
    )


@router.post(
    "/tasks/{task_id}/review-comments",
    response_model=ReviewCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_review_comment(
    task_id: UUID,
    request: ReviewCommentCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> ReviewCommentResponse:
    try:
        comment = DomainTaskService(session).create_review_comment(
            context.workspace.id,
            task_id,
            context.user.user_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return ReviewCommentResponse.model_validate(comment)


@router.post(
    "/tasks/{task_id}/revision-requests",
    response_model=RevisionRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_revision_request(
    task_id: UUID,
    request: RevisionRequestCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> RevisionRequestResponse:
    try:
        revision = DomainTaskService(session, queue).create_revision_request(
            context.workspace.id,
            task_id,
            context.user.user_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if revision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return RevisionRequestResponse.model_validate(revision)
