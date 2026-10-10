from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.capabilities.catalog.contracts import (
    CapabilityResourceResponse,
    EffectiveCapabilityCatalogResponse,
    WorkspaceCapabilityCatalogResponse,
)
from opsmesh.capabilities.catalog.effective import EffectiveCapabilityCatalogService
from opsmesh.capabilities.catalog.queries import WorkspaceCapabilityCatalogService
from opsmesh.capabilities.catalog.schemas import (
    CapabilityResourceCreateRequest,
    CapabilityResourceUpdateRequest,
    TeamCapabilityPolicyResponse,
    TeamCapabilityPolicyUpdateRequest,
)
from opsmesh.capabilities.governance.policy import TeamCapabilityPolicyService
from opsmesh.capabilities.references.service import CapabilityResourceService
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.db.errors import DatabaseConflictError
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.get("/catalog", response_model=WorkspaceCapabilityCatalogResponse)
def get_workspace_capability_catalog(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCapabilityCatalogResponse:
    return WorkspaceCapabilityCatalogService(session).build(context.workspace.id)


@router.put(
    "/teams/{team_id}/policy",
    response_model=TeamCapabilityPolicyResponse,
)
def update_team_capability_policy(
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
def get_effective_agent_capability_catalog(
    agent_profile_id: UUID,
    team_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> EffectiveCapabilityCatalogResponse:
    return EffectiveCapabilityCatalogService(session).resolve(
        workspace_id=context.workspace.id,
        agent_profile_id=agent_profile_id,
        user=context.user,
        team_id=team_id,
    )


@router.get("/resources", response_model=PageResponse[CapabilityResourceResponse])
def list_capability_resources(
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
def create_capability_resource(
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
def update_capability_resource(
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
def disable_capability_resource(
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
