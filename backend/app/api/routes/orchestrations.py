from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.orchestration import (
    OrchestrationDefinitionResponse,
    OrchestrationRevisionResponse,
    OrchestrationValidationResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.orchestration.definition_commands import (
    OrchestrationDefinitionCreate,
    OrchestrationDefinitionUpdate,
)
from backend.app.orchestration.definitions import (
    OrchestrationDefinitionError,
    OrchestrationDefinitionService,
)
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}/orchestrations", tags=["orchestrations"])


@router.get("", response_model=PageResponse[OrchestrationDefinitionResponse])
async def list_orchestrations(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[OrchestrationDefinitionResponse]:
    items, total = OrchestrationDefinitionService(session).list_definitions(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "",
    response_model=OrchestrationDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_orchestration(
    request: OrchestrationDefinitionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> OrchestrationDefinitionResponse:
    service = OrchestrationDefinitionService(session)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        definition = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="orchestrations.create",
            idempotency_key=idempotency_key,
            get_existing=lambda definition_id: service.get_definition(
                context.workspace.id,
                definition_id,
            ),
            create=lambda: service.create_definition(
                context.workspace.id,
                request,
                context.user.user_id,
            ),
            resource_id=lambda created: created.id,
        )
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except OrchestrationDefinitionError as exc:
        raise _http_error(exc) from exc
    return OrchestrationDefinitionResponse.model_validate(definition)


@router.get("/{orchestration_definition_id}", response_model=OrchestrationDefinitionResponse)
async def get_orchestration(
    orchestration_definition_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> OrchestrationDefinitionResponse:
    definition = OrchestrationDefinitionService(session).get_definition(
        context.workspace.id,
        orchestration_definition_id,
    )
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Orchestration not found")
    return OrchestrationDefinitionResponse.model_validate(definition)


@router.patch("/{orchestration_definition_id}", response_model=OrchestrationDefinitionResponse)
async def update_orchestration(
    orchestration_definition_id: UUID,
    request: OrchestrationDefinitionUpdate,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> OrchestrationDefinitionResponse:
    try:
        definition = OrchestrationDefinitionService(session).update_definition(
            context.workspace.id,
            orchestration_definition_id,
            request,
            context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except OrchestrationDefinitionError as exc:
        raise _http_error(exc) from exc
    return OrchestrationDefinitionResponse.model_validate(definition)


@router.post(
    "/{orchestration_definition_id}/validate",
    response_model=OrchestrationValidationResponse,
)
async def validate_orchestration(
    orchestration_definition_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> OrchestrationValidationResponse:
    try:
        definition, errors = OrchestrationDefinitionService(session).validate_definition(
            context.workspace.id,
            orchestration_definition_id,
        )
    except OrchestrationDefinitionError as exc:
        raise _http_error(exc) from exc
    return OrchestrationValidationResponse(
        workspace_id=definition.workspace_id,
        orchestration_definition_id=definition.id,
        version=definition.version,
        valid=not errors,
        errors=errors,
        definition=definition.definition,
    )


@router.post(
    "/{orchestration_definition_id}/publish",
    response_model=OrchestrationDefinitionResponse,
)
async def publish_orchestration(
    orchestration_definition_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> OrchestrationDefinitionResponse:
    try:
        definition = OrchestrationDefinitionService(session).publish_definition(
            context.workspace.id,
            orchestration_definition_id,
            context.user.user_id,
        )
    except OrchestrationDefinitionError as exc:
        raise _http_error(exc) from exc
    return OrchestrationDefinitionResponse.model_validate(definition)


@router.post(
    "/{orchestration_definition_id}/archive",
    response_model=OrchestrationDefinitionResponse,
)
async def archive_orchestration(
    orchestration_definition_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> OrchestrationDefinitionResponse:
    try:
        definition = OrchestrationDefinitionService(session).archive_definition(
            context.workspace.id,
            orchestration_definition_id,
            context.user.user_id,
        )
    except OrchestrationDefinitionError as exc:
        raise _http_error(exc) from exc
    return OrchestrationDefinitionResponse.model_validate(definition)


@router.get(
    "/{orchestration_definition_id}/revisions",
    response_model=PageResponse[OrchestrationRevisionResponse],
)
async def list_revisions(
    orchestration_definition_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[OrchestrationRevisionResponse]:
    try:
        items, total = OrchestrationDefinitionService(session).list_revisions(
            context.workspace.id,
            orchestration_definition_id,
            page,
        )
    except OrchestrationDefinitionError as exc:
        raise _http_error(exc) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{orchestration_definition_id}/revisions/{version}",
    response_model=OrchestrationRevisionResponse,
)
async def get_revision(
    orchestration_definition_id: UUID,
    version: int,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> OrchestrationRevisionResponse:
    revision = OrchestrationDefinitionService(session).get_revision(
        context.workspace.id,
        orchestration_definition_id,
        version,
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Orchestration revision not found")
    return OrchestrationRevisionResponse.model_validate(revision)


def _http_error(exc: OrchestrationDefinitionError) -> HTTPException:
    if exc.code == "orchestration_not_found":
        error_status = status.HTTP_404_NOT_FOUND
    elif exc.code in {
        "orchestration_task_has_steps",
        "orchestration_task_active_run",
        "orchestration_task_terminal",
        "orchestration_version_mismatch",
    }:
        error_status = status.HTTP_409_CONFLICT
    else:
        error_status = status.HTTP_400_BAD_REQUEST
    return HTTPException(
        status_code=error_status,
        detail={"code": exc.code, "message": str(exc)},
    )
