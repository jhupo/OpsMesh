from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.capabilities import (
    AgentToolPolicyDiagnosticsResponse,
    CapabilityCreateRequest,
    CapabilityResponse,
    McpCatalogServerResponse,
    McpCatalogToolPolicySummaryResponse,
    McpCatalogToolResponse,
    McpCatalogUsageResponse,
    McpCredentialReferenceCreateRequest,
    McpCredentialReferenceResponse,
    McpServerCreateRequest,
    McpServerHealthCheckRequest,
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
    WorkspaceCapabilityGovernanceApplyRequest,
    WorkspaceCapabilityGovernanceApplyResponse,
    WorkspaceCapabilityGovernanceResponse,
    WorkspaceSkillAvailabilityResponse,
    WorkspaceSkillImpactResponse,
    WorkspaceSkillInstallConfigRequest,
    WorkspaceSkillInstallRequest,
    WorkspaceSkillInstallResponse,
    WorkspaceSkillRollbackRequest,
    WorkspaceSkillToolAvailabilityResponse,
    WorkspaceSkillUpgradeRequest,
    WorkspaceToolPolicyMatrixResponse,
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


@router.get("/governance", response_model=WorkspaceCapabilityGovernanceResponse)
async def get_workspace_capability_governance(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCapabilityGovernanceResponse:
    diagnostics = CapabilityService(session).workspace_capability_governance(
        context.workspace.id,
    )
    return WorkspaceCapabilityGovernanceResponse.model_validate(diagnostics)


@router.post(
    "/governance/actions/apply",
    response_model=WorkspaceCapabilityGovernanceApplyResponse,
)
async def apply_workspace_capability_governance_actions(
    request: WorkspaceCapabilityGovernanceApplyRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCapabilityGovernanceApplyResponse:
    try:
        response = CapabilityService(session).apply_workspace_capability_governance_actions(
            workspace_id=context.workspace.id,
            actor_user_id=context.user.user_id,
            dry_run=request.dry_run,
            actions=request.actions,
            install_ids=request.install_ids,
            mcp_server_ids=request.mcp_server_ids,
            max_items=request.max_items,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WorkspaceCapabilityGovernanceApplyResponse.model_validate(response)


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
    "/workspace-skills/{install_id}/rollback",
    response_model=WorkspaceSkillInstallResponse,
)
async def rollback_workspace_skill(
    install_id: UUID,
    request: WorkspaceSkillRollbackRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> WorkspaceSkillInstallResponse:
    try:
        install = CapabilityService(session).rollback_skill_install(
            context.workspace.id,
            context.user.user_id,
            install_id,
            request,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
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


@router.get(
    "/workspace-skills/{install_id}/impact",
    response_model=WorkspaceSkillImpactResponse,
)
async def get_workspace_skill_impact(
    install_id: UUID,
    target_skill_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceSkillImpactResponse:
    try:
        impact = CapabilityService(session).workspace_skill_impact(
            context.workspace.id,
            install_id,
            target_skill_id=target_skill_id,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    impact = {
        **impact,
        "target_tool_availability": [
            WorkspaceSkillToolAvailabilityResponse(
                tool_name=tool.tool_name,
                available=tool.available,
                server_id=tool.server_id,
                server_name=tool.server_name,
                capability_key=tool.capability_key,
                requires_approval=tool.requires_approval,
                risk_level=tool.risk_level,
                blocked_reasons=tool.blocked_reasons,
            )
            for tool in impact["target_tool_availability"]
        ],
    }
    return WorkspaceSkillImpactResponse.model_validate(impact)


@router.get(
    "/workspace-skills/{install_id}/availability",
    response_model=WorkspaceSkillAvailabilityResponse,
)
async def get_workspace_skill_availability(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceSkillAvailabilityResponse:
    try:
        availability = CapabilityService(session).workspace_skill_availability(
            context.workspace.id,
            install_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WorkspaceSkillAvailabilityResponse(
        install_id=availability.install.id,
        installed_key=availability.install.installed_key,
        status=availability.install.status,
        usable=availability.usable,
        required_tools=availability.required_tools,
        tools=[
            WorkspaceSkillToolAvailabilityResponse(
                tool_name=tool.tool_name,
                available=tool.available,
                server_id=tool.server_id,
                server_name=tool.server_name,
                capability_key=tool.capability_key,
                requires_approval=tool.requires_approval,
                risk_level=tool.risk_level,
                blocked_reasons=tool.blocked_reasons,
            )
            for tool in availability.tools
        ],
        blocked_reasons=availability.blocked_reasons,
    )


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


@router.post("/mcp-servers/{mcp_server_id}/disable", response_model=McpServerResponse)
async def disable_mcp_server(
    mcp_server_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpServerResponse:
    try:
        server = CapabilityService(session).disable_mcp_server(
            context.workspace.id,
            mcp_server_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpServerResponse.model_validate(server)


@router.post("/mcp-servers/{mcp_server_id}/health-check", response_model=McpServerResponse)
async def record_mcp_server_health_check(
    mcp_server_id: UUID,
    request: McpServerHealthCheckRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpServerResponse:
    try:
        server = CapabilityService(session).record_mcp_server_health_check(
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
        allow = CapabilityService(session).disable_mcp_tool(
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


@router.get(
    "/agents/{agent_profile_id}/tool-policy-diagnostics",
    response_model=AgentToolPolicyDiagnosticsResponse,
)
async def get_agent_tool_policy_diagnostics(
    agent_profile_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentToolPolicyDiagnosticsResponse:
    try:
        diagnostics = CapabilityService(session).agent_tool_policy_diagnostics(
            context.workspace.id,
            agent_profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AgentToolPolicyDiagnosticsResponse.model_validate(diagnostics)


@router.get("/tool-policy-matrix", response_model=WorkspaceToolPolicyMatrixResponse)
async def get_workspace_tool_policy_matrix(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceToolPolicyMatrixResponse:
    matrix = CapabilityService(session).workspace_tool_policy_matrix(context.workspace.id)
    return WorkspaceToolPolicyMatrixResponse.model_validate(matrix)


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
                previous_secrets=settings.credential_encryption_previous_secrets,
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


@router.get("/mcp-tool-call-logs", response_model=PageResponse[McpToolCallLogResponse])
async def list_mcp_tool_call_logs(
    page: PageParams = Depends(pagination_params),
    mcp_server_id: UUID | None = Query(default=None),
    tool_name: str | None = Query(default=None, min_length=1, max_length=160),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[McpToolCallLogResponse]:
    try:
        items, total = CapabilityService(session).list_mcp_tool_call_logs(
            context.workspace.id,
            page,
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
            status=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


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
                policy_summary=McpCatalogToolPolicySummaryResponse.model_validate(
                    tool.policy_summary,
                ),
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
