from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.capabilities.catalog import (
    CapabilityResourceCreateRequest,
    CapabilityResourceResponse,
    CapabilityResourceUpdateRequest,
    EffectiveCapabilityCatalogResponse,
    TeamCapabilityPolicyResponse,
    TeamCapabilityPolicyUpdateRequest,
    WorkspaceCapabilityCatalogResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.capabilities.catalog_service import WorkspaceCapabilityCatalogService
from backend.app.capabilities.effective_catalog import EffectiveCapabilityCatalogService
from backend.app.capabilities.policy_service import TeamCapabilityPolicyService
from backend.app.capabilities.resource_service import CapabilityResourceService
from backend.app.core.pagination import PageParams
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.get("/catalog", response_model=WorkspaceCapabilityCatalogResponse)
async def get_workspace_capability_catalog(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCapabilityCatalogResponse:
    return WorkspaceCapabilityCatalogService(session).build(context.workspace.id)


@router.put(
    "/teams/{team_id}/policy",
    response_model=TeamCapabilityPolicyResponse,
)
async def update_team_capability_policy(
    team_id: UUID,
    request: TeamCapabilityPolicyUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> TeamCapabilityPolicyResponse:
    team = TeamCapabilityPolicyService(session).update_policy(
        workspace_id=context.workspace.id,
        team_id=team_id,
        policy=request.capability_policy,
        actor_user_id=context.user.user_id,
    )
    return TeamCapabilityPolicyResponse(
        workspace_id=context.workspace.id,
        team_id=team.id,
        capability_policy=team.capability_policy,
        capability_policy_version=team.capability_policy_version,
    )


@router.get(
    "/agents/{agent_profile_id}/effective-catalog",
    response_model=EffectiveCapabilityCatalogResponse,
)
async def get_effective_agent_capability_catalog(
    agent_profile_id: UUID,
    team_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> EffectiveCapabilityCatalogResponse:
    return EffectiveCapabilityCatalogService(session).build(
        workspace_id=context.workspace.id,
        agent_profile_id=agent_profile_id,
        team_id=team_id,
    )


@router.get("/resources", response_model=PageResponse[CapabilityResourceResponse])
async def list_capability_resources(
    page: PageParams = Depends(pagination_params),
    include_disabled: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[CapabilityResourceResponse]:
    resources, total = CapabilityResourceService(session).list_resources(
        context.workspace.id,
        page,
        include_disabled=include_disabled,
    )
    return PageResponse(
        items=[CapabilityResourceResponse.model_validate(item) for item in resources],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/resources",
    response_model=CapabilityResourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_capability_resource(
    request: CapabilityResourceCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> CapabilityResourceResponse:
    try:
        resource = CapabilityResourceService(session).create_resource(
            context.workspace.id,
            request,
            actor_user_id=context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return CapabilityResourceResponse.model_validate(resource)


@router.patch("/resources/{resource_id}", response_model=CapabilityResourceResponse)
async def update_capability_resource(
    resource_id: UUID,
    request: CapabilityResourceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> CapabilityResourceResponse:
    resource = CapabilityResourceService(session).update_resource(
        context.workspace.id,
        resource_id,
        request,
        actor_user_id=context.user.user_id,
    )
    return CapabilityResourceResponse.model_validate(resource)


@router.post(
    "/resources/{resource_id}/disable",
    response_model=CapabilityResourceResponse,
)
async def disable_capability_resource(
    resource_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> CapabilityResourceResponse:
    resource = CapabilityResourceService(session).disable_resource(
        context.workspace.id,
        resource_id,
        actor_user_id=context.user.user_id,
    )
    return CapabilityResourceResponse.model_validate(resource)
