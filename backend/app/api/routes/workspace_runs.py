from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.api.services.workspace_reads import WorkspaceReadService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session
from backend.app.orchestration.run_control import RunControlService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/runs", response_model=PageResponse[AgentRunResponse])
async def list_runs(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentRunResponse]:
    items, total = WorkspaceReadService(session).list_runs(
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
    items, total = WorkspaceReadService(session).list_run_events(
        context.workspace.id,
        agent_run_id,
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/runs/{agent_run_id}/cancel", response_model=AgentRunResponse)
async def cancel_run(
    agent_run_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentRunResponse:
    try:
        run = RunControlService(
            session=session,
            enqueue_run=RunOrchestrationService(session).enqueue_run,
        ).cancel_run(
            workspace_id=context.workspace.id,
            run_id=agent_run_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return AgentRunResponse.model_validate(run)


@router.post(
    "/runs/{agent_run_id}/retry",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def retry_run(
    agent_run_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AgentRunResponse:
    run_orchestration = RunOrchestrationService(session, queue=queue)
    try:
        run = RunControlService(
            session=session,
            enqueue_run=run_orchestration.enqueue_run,
        ).retry_failed_run(
            workspace_id=context.workspace.id,
            run_id=agent_run_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return AgentRunResponse.model_validate(run)


@router.get("/audit-events", response_model=PageResponse[AuditEventResponse])
async def list_audit_events(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AuditEventResponse]:
    items, total = WorkspaceReadService(session).list_audit_events(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)
