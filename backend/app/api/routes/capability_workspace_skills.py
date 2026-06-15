from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.capabilities import (
    WorkspaceSkillAvailabilityResponse,
    WorkspaceSkillImpactResponse,
    WorkspaceSkillInstallConfigRequest,
    WorkspaceSkillInstallRequest,
    WorkspaceSkillInstallResponse,
    WorkspaceSkillRollbackRequest,
    WorkspaceSkillToolAvailabilityResponse,
    WorkspaceSkillUpgradeRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.capabilities.skill_tool_diagnostics import SkillToolDiagnosticsService
from backend.app.capabilities.workspace_skill_impact import WorkspaceSkillImpactService
from backend.app.capabilities.workspace_skill_lifecycle import WorkspaceSkillLifecycleService
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.get("/workspace-skills", response_model=PageResponse[WorkspaceSkillInstallResponse])
async def list_workspace_skills(
    page: PageParams = Depends(pagination_params),
    include_disabled: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceSkillInstallResponse]:
    items, total = WorkspaceSkillLifecycleService(session).list_workspace_skills(
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
        install = WorkspaceSkillLifecycleService(session).install_skill(
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
        install = WorkspaceSkillLifecycleService(session).install_skill_by_id(
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
        install = WorkspaceSkillLifecycleService(session).upgrade_skill_install(
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
        install = WorkspaceSkillLifecycleService(session).rollback_skill_install(
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
        install = WorkspaceSkillLifecycleService(session).disable_skill_install(
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
        impact = WorkspaceSkillImpactService(session).workspace_skill_impact(
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
            _workspace_skill_tool_availability_response(tool)
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
        availability = SkillToolDiagnosticsService(session).workspace_skill_availability(
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
            _workspace_skill_tool_availability_response(tool)
            for tool in availability.tools
        ],
        blocked_reasons=availability.blocked_reasons,
    )


def _workspace_skill_tool_availability_response(
    tool: object,
) -> WorkspaceSkillToolAvailabilityResponse:
    return WorkspaceSkillToolAvailabilityResponse(
        tool_name=tool.tool_name,
        available=tool.available,
        server_id=tool.server_id,
        server_name=tool.server_name,
        capability_key=tool.capability_key,
        requires_approval=tool.requires_approval,
        risk_level=tool.risk_level,
        blocked_reasons=tool.blocked_reasons,
    )
