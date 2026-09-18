from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.dependencies.auth import workspace_dependency
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.capabilities.mcp_catalog import (
    McpCatalogServerResponse,
    McpCatalogToolPolicySummaryResponse,
    McpCatalogToolResponse,
    McpCatalogUsageResponse,
    McpToolDescriptor,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.db.errors import DatabaseConflictError
from backend.app.core.db.session import get_db_session
from backend.app.core.pagination import PageParams
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.access.context import WorkspaceContext
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.capabilities.mcp.catalog.catalog import McpCatalogServer, McpCatalogUsage
from backend.app.domains.capabilities.mcp.catalog.contracts import (
    McpServerCreateRequest,
    McpServerDiscoveryRequest,
    McpServerDiscoveryResponse,
    McpServerHealthCheckRequest,
    McpServerResponse,
    McpServerUpdateRequest,
    McpToolAllowRequest,
    McpToolAllowResponse,
    McpToolAllowUpdateRequest,
)
from backend.app.domains.capabilities.mcp.catalog.discovery import (
    McpDiscoveryConflict,
    McpDiscoveryError,
    McpToolDiscoveryService,
)
from backend.app.domains.capabilities.mcp.catalog.servers import McpServerService

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.get("/mcp-servers", response_model=PageResponse[McpServerResponse])
async def list_mcp_servers(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[McpServerResponse]:
    items, total = McpServerService(session).list_mcp_servers(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/mcp-servers", response_model=McpServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    request: McpServerCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpServerResponse:
    try:
        server = McpServerService(session, settings=settings).create_mcp_server(
            context.workspace.id,
            request,
            context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return McpServerResponse.model_validate(server)


@router.patch("/mcp-servers/{mcp_server_id}", response_model=McpServerResponse)
async def update_mcp_server(
    mcp_server_id: UUID,
    request: McpServerUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpServerResponse:
    try:
        server = McpServerService(session, settings=settings).update_mcp_server(
            context.workspace.id,
            mcp_server_id,
            request,
            context.user.user_id,
        )
    except ValueError as exc:
        detail = str(exc)
        error_status = (
            status.HTTP_404_NOT_FOUND
            if detail == "MCP server not found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=error_status, detail=detail) from exc
    return McpServerResponse.model_validate(server)


@router.get("/mcp-catalog", response_model=PageResponse[McpCatalogServerResponse])
async def list_mcp_catalog(
    page: PageParams = Depends(pagination_params),
    agent_profile_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[McpCatalogServerResponse]:
    try:
        items, total = McpServerService(session).list_mcp_catalog(
            context.workspace.id,
            page,
            agent_profile_id=agent_profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(
        items=[_mcp_catalog_response(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/mcp-servers/{mcp_server_id}/discover",
    response_model=McpServerDiscoveryResponse,
)
async def discover_mcp_server_tools(
    mcp_server_id: UUID,
    request: McpServerDiscoveryRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpServerDiscoveryResponse:
    try:
        result = await McpToolDiscoveryService(
            session,
            secret_service=SecretEncryptionService(
                secret=settings.credential_encryption_secret,
                key_id=settings.credential_encryption_key_id,
                previous_secrets=settings.credential_encryption_previous_secrets,
            ),
            timeout_seconds=settings.mcp_tool_timeout_seconds,
        ).discover(
            workspace_id=context.workspace.id,
            server_id=mcp_server_id,
            credential_id=request.credential_id,
            actor_user_id=context.user.user_id,
        )
    except McpDiscoveryError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
                if isinstance(exc, McpDiscoveryConflict)
                else status.HTTP_404_NOT_FOUND
                if str(exc) == "MCP server not found or inactive"
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(exc),
        ) from exc
    return McpServerDiscoveryResponse.model_validate(result)


@router.get(
    "/mcp-servers/{mcp_server_id}/discovered-tools",
    response_model=list[McpToolAllowResponse],
)
async def list_discovered_mcp_tools(
    mcp_server_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> list[McpToolAllowResponse]:
    service = McpServerService(session)
    try:
        rows = service.discovered_mcp_tools(context.workspace.id, mcp_server_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [McpToolAllowResponse.model_validate(row) for row in rows]


@router.post(
    "/mcp-servers/{mcp_server_id}/tools",
    response_model=McpToolAllowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def allow_mcp_tool(
    mcp_server_id: UUID,
    request: McpToolAllowRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpToolAllowResponse:
    try:
        allow = McpServerService(session, settings=settings).allow_mcp_tool(
            context.workspace.id,
            mcp_server_id,
            request,
            context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpToolAllowResponse.model_validate(allow)


@router.post(
    "/mcp-servers/{mcp_server_id}/tools/{allowlist_id}/enable",
    response_model=McpToolAllowResponse,
)
async def enable_discovered_mcp_tool(
    mcp_server_id: UUID,
    allowlist_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpToolAllowResponse:
    try:
        allow = McpServerService(session, settings=settings).enable_discovered_mcp_tool(
            context.workspace.id,
            mcp_server_id,
            allowlist_id,
            context.user.user_id,
        )
    except ValueError as exc:
        error_status = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(exc).lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=error_status, detail=str(exc)) from exc
    return McpToolAllowResponse.model_validate(allow)


@router.post("/mcp-servers/{mcp_server_id}/disable", response_model=McpServerResponse)
async def disable_mcp_server(
    mcp_server_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpServerResponse:
    try:
        server = McpServerService(session).disable_mcp_server(
            context.workspace.id,
            mcp_server_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpServerResponse.model_validate(server)


@router.patch(
    "/mcp-servers/{mcp_server_id}/tools/{allowlist_id}",
    response_model=McpToolAllowResponse,
)
async def update_mcp_tool(
    mcp_server_id: UUID,
    allowlist_id: UUID,
    request: McpToolAllowUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpToolAllowResponse:
    try:
        allow = McpServerService(session, settings=settings).update_mcp_tool(
            context.workspace.id,
            mcp_server_id,
            allowlist_id,
            request,
            context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        message = str(exc)
        error_status = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=error_status, detail=message) from exc
    return McpToolAllowResponse.model_validate(allow)


@router.post("/mcp-servers/{mcp_server_id}/health-check", response_model=McpServerResponse)
async def record_mcp_server_health_check(
    mcp_server_id: UUID,
    request: McpServerHealthCheckRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpServerResponse:
    try:
        server = McpServerService(session).record_mcp_server_health_check(
            context.workspace.id,
            mcp_server_id,
            context.user.user_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpServerResponse.model_validate(server)


@router.post(
    "/mcp-servers/{mcp_server_id}/tools/{allowlist_id}/disable",
    response_model=McpToolAllowResponse,
)
async def disable_mcp_tool(
    mcp_server_id: UUID,
    allowlist_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpToolAllowResponse:
    try:
        allow = McpServerService(session).disable_mcp_tool(
            context.workspace.id,
            mcp_server_id,
            allowlist_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpToolAllowResponse.model_validate(allow)


@router.get("/mcp-tools", response_model=list[McpToolDescriptor])
async def list_mapped_mcp_tools(
    agent_profile_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> list[McpToolDescriptor]:
    service = McpServerService(session)
    try:
        rows = (
            service.mcp_tools_for_agent(context.workspace.id, agent_profile_id)
            if agent_profile_id is not None
            else service.list_allowed_mcp_tools(context.workspace.id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [
        McpToolDescriptor(
            server_id=server.id,
            server_name=server.name,
            tool_name=allow.tool_name,
            title=allow.title,
            description=allow.description,
            input_schema=allow.input_schema,
            output_schema=allow.output_schema,
            capability_key=allow.capability_key,
            requires_approval=allow.requires_approval,
            risk_level=allow.risk_level,
            policy=allow.policy,
        )
        for allow, server in rows
    ]


def _mcp_catalog_response(item: McpCatalogServer) -> McpCatalogServerResponse:
    server = item.server
    return McpCatalogServerResponse(
        id=server.id,
        name=server.name,
        server_type=server.server_type,
        visibility=server.visibility,
        status=server.status,
        health_status=server.health_status,
        last_health_check_at=server.last_health_check_at,
        last_error=server.last_error,
        discovery_status=server.discovery_status,
        discovery_version=server.discovery_version,
        discovered_at=server.discovered_at,
        discovery_checksum=server.discovery_checksum,
        discovery_error=server.discovery_error,
        execution_mode=item.execution_mode,
        executable=item.executable,
        blocked_reasons=item.blocked_reasons,
        credential_status=item.credential_status,
        credential_count=item.credential_count,
        workspace_credential_count=item.workspace_credential_count,
        connection_summary=item.connection_summary,
        usage=_mcp_catalog_usage_response(item.usage),
        tools=[
            McpCatalogToolResponse(
                id=tool.allowlist.id,
                tool_name=tool.allowlist.tool_name,
                title=tool.allowlist.title,
                description=tool.allowlist.description,
                input_schema=tool.allowlist.input_schema,
                output_schema=tool.allowlist.output_schema,
                capability_key=tool.allowlist.capability_key,
                requires_approval=tool.allowlist.requires_approval,
                risk_level=tool.allowlist.risk_level,
                policy=tool.allowlist.policy,
                policy_summary=McpCatalogToolPolicySummaryResponse.model_validate(
                    tool.policy_summary,
                ),
                status=tool.allowlist.status,
                discovery_source=tool.allowlist.discovery_source,
                discovery_status=tool.allowlist.discovery_status,
                discovery_checksum=tool.allowlist.discovery_checksum,
                discovered_at=tool.allowlist.discovered_at,
                usage=_mcp_catalog_usage_response(tool.usage),
            )
            for tool in item.tools
        ],
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _mcp_catalog_usage_response(item: McpCatalogUsage) -> McpCatalogUsageResponse:
    return McpCatalogUsageResponse(
        call_count=item.call_count,
        failed_call_count=item.failed_call_count,
        last_call_at=item.last_call_at,
        last_call_status=item.last_call_status,
        last_error_code=item.last_error_code,
    )
