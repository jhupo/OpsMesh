from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.capabilities.base import (
    CapabilityCreateRequest,
    CapabilityResponse,
    SkillCreateRequest,
    SkillResponse,
    ToolGroupCreateRequest,
    ToolGroupResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.capabilities.service import CapabilityService
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session

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
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> CapabilityResponse:
    try:
        capability = CapabilityService(session, settings=settings).create_capability(
            request,
            context.workspace.id,
            actor_user_id=context.user.user_id,
        )
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
    settings: Settings = Depends(get_settings),
) -> SkillResponse:
    try:
        skill = CapabilityService(session, settings=settings).create_skill(
            request,
            context.workspace.id,
            actor_user_id=context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return SkillResponse.model_validate(skill)


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
