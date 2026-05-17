from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.agents import AgentProfileCreateRequest, AgentProfileResponse
from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.api.schemas.tasks import TaskCreateRequest, TaskResponse
from backend.app.api.schemas.teams import AgentTeamCreateRequest, AgentTeamResponse
from backend.app.api.services.resources import WorkspaceResourceService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/agents", response_model=PageResponse[AgentProfileResponse])
async def list_agents(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentProfileResponse]:
    items, total = WorkspaceResourceService(session).list_agents(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/agents", response_model=AgentProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    request: AgentProfileCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    agent = WorkspaceResourceService(session).create_agent(context.workspace.id, request)
    return AgentProfileResponse.model_validate(agent)


@router.get("/teams", response_model=PageResponse[AgentTeamResponse])
async def list_teams(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentTeamResponse]:
    items, total = WorkspaceResourceService(session).list_teams(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/teams", response_model=AgentTeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    request: AgentTeamCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamResponse:
    team = WorkspaceResourceService(session).create_team(context.workspace.id, request)
    return AgentTeamResponse.model_validate(team)


@router.get("/tasks", response_model=PageResponse[TaskResponse])
async def list_tasks(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskResponse]:
    items, total = WorkspaceResourceService(session).list_tasks(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    request: TaskCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    task = WorkspaceResourceService(session).create_task(
        workspace_id=context.workspace.id,
        created_by_user_id=context.user.user_id,
        data=request,
    )
    return TaskResponse.model_validate(task)


@router.get("/runs", response_model=PageResponse[AgentRunResponse])
async def list_runs(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentRunResponse]:
    items, total = WorkspaceResourceService(session).list_runs(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/runs/{agent_run_id}/events", response_model=PageResponse[RunEventResponse])
async def list_run_events(
    agent_run_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RunEventResponse]:
    items, total = WorkspaceResourceService(session).list_run_events(
        context.workspace.id,
        agent_run_id,
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/audit-events", response_model=PageResponse[AuditEventResponse])
async def list_audit_events(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AuditEventResponse]:
    items, total = WorkspaceResourceService(session).list_audit_events(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)
