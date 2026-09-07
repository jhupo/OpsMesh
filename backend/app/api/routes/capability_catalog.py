from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.capabilities.catalog import (
    CapabilityResourceCreateRequest,
    CapabilityResourceResponse,
    CapabilityResourceUpdateRequest,
    CapabilityToolDescriptor,
    WorkspaceCapabilityCatalogResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.capabilities.mcp_servers import McpServerService
from backend.app.capabilities.product_tool_catalog import PRODUCT_TOOL_CATALOG
from backend.app.capabilities.resource_service import CapabilityResourceService
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.get("/catalog", response_model=WorkspaceCapabilityCatalogResponse)
async def get_workspace_capability_catalog(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCapabilityCatalogResponse:
    resources = CapabilityResourceService(session).list_active_resources(context.workspace.id)
    mcp_tools = McpServerService(session).list_allowed_mcp_tools(context.workspace.id)
    product_descriptors = [
        CapabilityToolDescriptor(
            name=definition.name,
            source="product",
            description=definition.description,
            input_schema=definition.input_schema,
            requires_approval=definition.requires_approval,
            risk_level=definition.risk_level,
        )
        for definition in PRODUCT_TOOL_CATALOG
    ]
    mcp_descriptors = [
        CapabilityToolDescriptor(
            name=allow.tool_name,
            source="mcp",
            description=allow.description,
            input_schema=allow.input_schema,
            requires_approval=allow.requires_approval,
            risk_level=allow.risk_level,
            capability_key=allow.capability_key,
            mcp_server_id=server.id,
            mcp_server_name=server.name,
            policy=allow.policy,
        )
        for allow, server in mcp_tools
    ]
    return WorkspaceCapabilityCatalogResponse(
        workspace_id=context.workspace.id,
        tools=sorted(product_descriptors + mcp_descriptors, key=lambda item: item.name),
        resources=[CapabilityResourceResponse.model_validate(item) for item in resources],
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
