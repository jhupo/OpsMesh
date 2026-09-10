from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.agents.model_provider_summary import agent_profile_response
from backend.app.agents.service import AgentManagementService
from backend.app.api.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.routes.agent_errors import agent_management_http_error
from backend.app.api.schemas.agents import (
    AgentProfileCloneRequest,
    AgentProfileCreateRequest,
    AgentProfileResponse,
    AgentProfileRollbackRequest,
    AgentProfileUpdateRequest,
    AgentProfileVersionResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/agents", response_model=PageResponse[AgentProfileResponse])
async def list_agents(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentProfileResponse]:
    items, total = AgentManagementService(session).list_agents(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(
        items=[agent_profile_response(session, item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/agents/{agent_id}", response_model=AgentProfileResponse)
async def get_agent(
    agent_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    agent = AgentManagementService(session).get_agent(context.workspace.id, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.post("/agents", response_model=AgentProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    request: AgentProfileCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentProfileResponse:
    agent_service = AgentManagementService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        agent = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="agents.create",
            idempotency_key=idempotency_key,
            get_existing=lambda agent_id: agent_service.get_agent(
                context.workspace.id,
                agent_id,
            ),
            create=lambda: agent_service.create_agent(
                context.workspace.id,
                request,
                context.user.user_id,
            ),
            resource_id=lambda created_agent: created_agent.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    return agent_profile_response(session, agent)


@router.patch("/agents/{agent_id}", response_model=AgentProfileResponse)
async def update_agent(
    agent_id: UUID,
    request: AgentProfileUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        agent = AgentManagementService(session).update_agent(
            context.workspace.id,
            agent_id,
            request,
            context.user.user_id,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.post("/agents/{agent_id}/archive", response_model=AgentProfileResponse)
async def archive_agent(
    agent_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        agent = AgentManagementService(session).archive_agent(
            context.workspace.id,
            agent_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.post("/agents/{agent_id}/activate", response_model=AgentProfileResponse)
async def activate_agent(
    agent_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        agent = AgentManagementService(session).activate_agent(
            context.workspace.id,
            agent_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> Response:
    try:
        deleted = AgentManagementService(session).delete_agent(
            context.workspace.id,
            agent_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc
    if deleted is False:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/agents/{agent_id}/clone",
    response_model=AgentProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_agent(
    agent_id: UUID,
    request: AgentProfileCloneRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentProfileResponse:
    agent_service = AgentManagementService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        agent = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation=f"agents.{agent_id}.clone",
            idempotency_key=idempotency_key,
            get_existing=lambda cloned_agent_id: agent_service.get_agent(
                context.workspace.id,
                cloned_agent_id,
            ),
            create=lambda: agent_service.clone_agent(
                context.workspace.id,
                agent_id,
                request or AgentProfileCloneRequest(),
                context.user.user_id,
            ),
            resource_id=lambda created_agent: created_agent.id,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    return agent_profile_response(session, agent)


@router.get(
    "/agents/{agent_id}/versions",
    response_model=PageResponse[AgentProfileVersionResponse],
)
async def list_agent_versions(
    agent_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentProfileVersionResponse]:
    try:
        items, total = AgentManagementService(session).list_agent_versions(
            context.workspace.id,
            agent_id,
            page,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/agents/{agent_id}/versions/{version}/rollback",
    response_model=AgentProfileResponse,
)
async def rollback_agent_version(
    agent_id: UUID,
    version: int = Path(ge=1),
    request: AgentProfileRollbackRequest | None = None,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        agent = AgentManagementService(session).rollback_agent_version(
            context.workspace.id,
            agent_id,
            version,
            request or AgentProfileRollbackRequest(),
            context.user.user_id,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_profile_response(session, agent)
