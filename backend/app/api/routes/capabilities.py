from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.capabilities import (
    CapabilityCreateRequest,
    CapabilityResponse,
    McpCatalogServerResponse,
    McpCatalogToolResponse,
    McpCatalogUsageResponse,
    McpCredentialReferenceCreateRequest,
    McpCredentialReferenceResponse,
    McpServerCreateRequest,
    McpServerResponse,
    McpToolAllowRequest,
    McpToolAllowResponse,
    McpToolCallLogRequest,
    McpToolCallLogResponse,
    McpToolDescriptor,
    SkillCreateRequest,
    SkillResponse,
    ToolGroupCreateRequest,
    ToolGroupResponse,
    WorkspaceSkillInstallConfigRequest,
    WorkspaceSkillInstallRequest,
    WorkspaceSkillInstallResponse,
    WorkspaceSkillUpgradeRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.capabilities.service import CapabilityService
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.secrets.service import SecretEncryptionService

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.get("", response_model=PageResponse[CapabilityResponse])
async def list_capabilities(
    page: PageParams = Depends(pagination_params),
    category: str | None = Query(default=None),
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[CapabilityResponse]:
    items, total = CapabilityService(session).list_capabilities(page, category)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=CapabilityResponse, status_code=status.HTTP_201_CREATED)
async def create_capability(
    request: CapabilityCreateRequest,
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> CapabilityResponse:
    try:
        capability = CapabilityService(session).create_capability(request)
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return CapabilityResponse.model_validate(capability)


@router.get("/skills", response_model=PageResponse[SkillResponse])
async def list_skills(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[SkillResponse]:
    items, total = CapabilityService(session).list_skills(page, context.workspace.id)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/skills", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(
    request: SkillCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> SkillResponse:
    try:
        skill = CapabilityService(session).create_skill(request, context.workspace.id)
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return SkillResponse.model_validate(skill)


@router.get("/workspace-skills", response_model=PageResponse[WorkspaceSkillInstallResponse])
async def list_workspace_skills(
    page: PageParams = Depends(pagination_params),
    include_disabled: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceSkillInstallResponse]:
    items, total = CapabilityService(session).list_workspace_skills(
        context.workspace.id,
        page,
        include_disabled=include_disabled,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/workspace-skills",
    response_model=WorkspaceSkillInstallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def install_workspace_skill(
    request: WorkspaceSkillInstallRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> WorkspaceSkillInstallResponse:
    try:
        install = CapabilityService(session).install_skill(
            context.workspace.id,
            context.user.user_id,
            request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WorkspaceSkillInstallResponse.model_validate(install)


@router.post(
    "/skills/{skill_id}/install",
    response_model=WorkspaceSkillInstallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def install_skill_by_id(
    skill_id: UUID,
    request: WorkspaceSkillInstallConfigRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> WorkspaceSkillInstallResponse:
    try:
        install = CapabilityService(session).install_skill_by_id(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            skill_id=skill_id,
            config=request.config,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WorkspaceSkillInstallResponse.model_validate(install)


@router.post(
    "/workspace-skills/{install_id}/upgrade",
    response_model=WorkspaceSkillInstallResponse,
)
async def upgrade_workspace_skill(
    install_id: UUID,
    request: WorkspaceSkillUpgradeRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> WorkspaceSkillInstallResponse:
    try:
        install = CapabilityService(session).upgrade_skill_install(
            context.workspace.id,
            context.user.user_id,
            install_id,
            request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WorkspaceSkillInstallResponse.model_validate(install)


@router.post(
    "/workspace-skills/{install_id}/disable",
    response_model=WorkspaceSkillInstallResponse,
)
async def disable_workspace_skill(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> WorkspaceSkillInstallResponse:
    try:
        install = CapabilityService(session).disable_skill_install(
            context.workspace.id,
            context.user.user_id,
            install_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WorkspaceSkillInstallResponse.model_validate(install)


@router.get("/tool-groups", response_model=PageResponse[ToolGroupResponse])
async def list_tool_groups(
    page: PageParams = Depends(pagination_params),
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[ToolGroupResponse]:
    items, total = CapabilityService(session).list_tool_groups(page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/tool-groups", response_model=ToolGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_tool_group(
    request: ToolGroupCreateRequest,
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> ToolGroupResponse:
    try:
        group = CapabilityService(session).create_tool_group(request)
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return ToolGroupResponse.model_validate(group)


@router.get("/mcp-servers", response_model=PageResponse[McpServerResponse])
async def list_mcp_servers(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[McpServerResponse]:
    items, total = CapabilityService(session).list_mcp_servers(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/mcp-servers", response_model=McpServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    request: McpServerCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpServerResponse:
    try:
        server = CapabilityService(session).create_mcp_server(
            context.workspace.id,
            request,
            context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return McpServerResponse.model_validate(server)


@router.get("/mcp-catalog", response_model=PageResponse[McpCatalogServerResponse])
async def list_mcp_catalog(
    page: PageParams = Depends(pagination_params),
    agent_profile_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[McpCatalogServerResponse]:
    try:
        items, total = CapabilityService(session).list_mcp_catalog(
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
    "/mcp-servers/{mcp_server_id}/tools",
    response_model=McpToolAllowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def allow_mcp_tool(
    mcp_server_id: UUID,
    request: McpToolAllowRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpToolAllowResponse:
    try:
        allow = CapabilityService(session).allow_mcp_tool(
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


@router.get("/mcp-tools", response_model=list[McpToolDescriptor])
async def list_mapped_mcp_tools(
    agent_profile_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> list[McpToolDescriptor]:
    service = CapabilityService(session)
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
            capability_key=allow.capability_key,
            requires_approval=allow.requires_approval,
            risk_level=allow.risk_level,
            policy=allow.policy,
        )
        for allow, server in rows
    ]


@router.post(
    "/mcp-credentials",
    response_model=McpCredentialReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcp_credential_reference(
    request: McpCredentialReferenceCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpCredentialReferenceResponse:
    try:
        credential = CapabilityService(
            session,
            SecretEncryptionService(
                secret=settings.credential_encryption_secret,
                key_id=settings.credential_encryption_key_id,
            ),
        ).create_credential_reference(
            context.workspace.id,
            request,
            context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpCredentialReferenceResponse.model_validate(credential)


@router.get("/mcp-credentials", response_model=PageResponse[McpCredentialReferenceResponse])
async def list_mcp_credential_references(
    page: PageParams = Depends(pagination_params),
    mcp_server_id: UUID | None = Query(default=None),
    include_disabled: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[McpCredentialReferenceResponse]:
    try:
        items, total = CapabilityService(session).list_credential_references(
            context.workspace.id,
            page,
            mcp_server_id=mcp_server_id,
            include_disabled=include_disabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/mcp-credentials/{credential_id}/disable",
    response_model=McpCredentialReferenceResponse,
)
async def disable_mcp_credential_reference(
    credential_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpCredentialReferenceResponse:
    try:
        credential = CapabilityService(session).disable_credential_reference(
            context.workspace.id,
            credential_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpCredentialReferenceResponse.model_validate(credential)


@router.post(
    "/mcp-tool-call-logs",
    response_model=McpToolCallLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def log_mcp_tool_call(
    request: McpToolCallLogRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> McpToolCallLogResponse:
    try:
        log = CapabilityService(session).log_mcp_tool_call(context.workspace.id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpToolCallLogResponse.model_validate(log)


def _mcp_catalog_response(item: object) -> McpCatalogServerResponse:
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
                capability_key=tool.allowlist.capability_key,
                requires_approval=tool.allowlist.requires_approval,
                risk_level=tool.allowlist.risk_level,
                policy=tool.allowlist.policy,
                status=tool.allowlist.status,
                usage=_mcp_catalog_usage_response(tool.usage),
            )
            for tool in item.tools
        ],
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _mcp_catalog_usage_response(item: object) -> McpCatalogUsageResponse:
    return McpCatalogUsageResponse(
        call_count=item.call_count,
        failed_call_count=item.failed_call_count,
        last_call_at=item.last_call_at,
        last_call_status=item.last_call_status,
        last_error_code=item.last_error_code,
    )
