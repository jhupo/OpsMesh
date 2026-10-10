from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from redis import Redis
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import (
    account_action_dependency,
    get_current_user,
    workspace_dependency,
)
from opsmesh.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from opsmesh.identity.authorization.permissions import AccountAction, WorkspaceAction
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.errors import DatabaseConflictError
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.redis.dependencies import get_redis_client
from opsmesh.shared.redis.keys import RedisKeyBuilder
from opsmesh.workspaces.management.health.service import WorkspaceHealthService
from opsmesh.workspaces.management.schemas import (
    WorkspaceCreateRequest,
    WorkspaceHealthResponse,
    WorkspaceHealthSnapshotResponse,
    WorkspaceHealthTrendResponse,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from opsmesh.workspaces.management.service import WorkspaceService

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=PageResponse[WorkspaceResponse])
def list_workspaces(
    page: PageParams = Depends(pagination_params),
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceResponse]:
    items, total = WorkspaceService(session).list_for_user(
        current_user.user_id,
        page,
        allowed_workspace_ids=current_user.allowed_workspace_ids,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(
    request: WorkspaceCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: AuthenticatedUser = Depends(
        account_action_dependency(AccountAction.WORKSPACES_CREATE)
    ),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> WorkspaceResponse:
    service = WorkspaceService(session)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        workspace = run_idempotent_create(
            idempotency=idempotency,
            scope_id=current_user.user_id,
            operation="workspaces.create",
            idempotency_key=idempotency_key,
            get_existing=lambda workspace_id: service.get_owned(
                current_user.user_id,
                workspace_id,
            ),
            create=lambda: service.create_for_owner(current_user.user_id, request),
            resource_id=lambda created_workspace: created_workspace.id,
        )
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceResponse.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
) -> WorkspaceResponse:
    return WorkspaceResponse.model_validate(context.workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
def update_workspace(
    request: WorkspaceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> WorkspaceResponse:
    workspace = WorkspaceService(session).get_scoped(context.workspace.id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    try:
        updated = WorkspaceService(session).update(
            workspace,
            request,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    return WorkspaceResponse.model_validate(updated)


@router.get("/{workspace_id}/health", response_model=WorkspaceHealthResponse)
def get_workspace_health(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceHealthResponse:
    health = WorkspaceHealthService(session).get_health(context.workspace.id)
    return WorkspaceHealthResponse.model_validate(health)


@router.post(
    "/{workspace_id}/health/snapshots",
    response_model=WorkspaceHealthSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_health_snapshot(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> WorkspaceHealthSnapshotResponse:
    snapshot = WorkspaceHealthService(session).record_snapshot(context.workspace.id)
    return WorkspaceHealthSnapshotResponse.model_validate(snapshot)


@router.get(
    "/{workspace_id}/health/snapshots",
    response_model=PageResponse[WorkspaceHealthSnapshotResponse],
)
def list_workspace_health_snapshots(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceHealthSnapshotResponse]:
    snapshots, total = WorkspaceHealthService(session).list_snapshots(
        context.workspace.id,
        limit=limit,
        offset=offset,
    )
    return PageResponse(items=snapshots, total=total, limit=limit, offset=offset)


@router.get(
    "/{workspace_id}/health/trends",
    response_model=WorkspaceHealthTrendResponse,
)
def get_workspace_health_trends(
    limit: int = Query(default=20, ge=2, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceHealthTrendResponse:
    trends = WorkspaceHealthService(session).get_trends(context.workspace.id, limit=limit)
    return WorkspaceHealthTrendResponse.model_validate(trends)
