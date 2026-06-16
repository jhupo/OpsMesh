from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.workspaces import (
    WorkspaceCreateRequest,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from backend.app.api.services.workspaces import WorkspaceService
from backend.app.auth.context import AuthenticatedUser, WorkspaceContext
from backend.app.auth.dependencies import get_current_user, workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=PageResponse[WorkspaceResponse])
async def list_workspaces(
    page: PageParams = Depends(pagination_params),
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceResponse]:
    items, total = WorkspaceService(session).list_for_user(current_user.user_id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    request: WorkspaceCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: AuthenticatedUser = Depends(get_current_user),
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
async def get_workspace(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
) -> WorkspaceResponse:
    return WorkspaceResponse.model_validate(context.workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
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
