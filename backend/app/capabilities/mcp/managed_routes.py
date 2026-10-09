from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.capabilities.mcp.managed_schemas import (
    ManagedMcpActionRequest,
    ManagedMcpCreateRequest,
    ManagedMcpResponse,
)
from backend.app.capabilities.mcp.managed_service import ManagedMcpService
from backend.app.identity.auth.dependencies import workspace_dependency
from backend.app.identity.authorization.context import WorkspaceContext
from backend.app.identity.authorization.permissions import WorkspaceAction
from backend.app.runtime.queues.dependencies import get_worker_queue
from backend.app.runtime.queues.service import RedisQueue
from backend.app.shared.config import Settings, get_settings
from backend.app.shared.db.errors import DatabaseConflictError
from backend.app.shared.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.post("/managed-mcp", response_model=list[ManagedMcpResponse], status_code=202)
def create_managed_mcp(
    request: ManagedMcpCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    queue: RedisQueue = Depends(get_worker_queue),
) -> list[ManagedMcpResponse]:
    try:
        rows = ManagedMcpService(session, settings, queue).create(
            context.workspace.id, context.user, request
        )
    except DatabaseConflictError as exc:
        raise HTTPException(409, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [ManagedMcpResponse.model_validate(row) for row in rows]


@router.get("/mcp-servers/{server_id}/deployment", response_model=ManagedMcpResponse)
def get_managed_mcp(
    server_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    queue: RedisQueue = Depends(get_worker_queue),
) -> ManagedMcpResponse:
    row = ManagedMcpService(session, settings, queue).get(context.workspace.id, server_id)
    return ManagedMcpResponse.model_validate(row)


@router.post(
    "/mcp-servers/{server_id}/deployment", response_model=ManagedMcpResponse, status_code=202
)
def control_managed_mcp(
    server_id: UUID,
    request: ManagedMcpActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    queue: RedisQueue = Depends(get_worker_queue),
) -> ManagedMcpResponse:
    row = ManagedMcpService(session, settings, queue).control(
        context.workspace.id, server_id, context.user, request.action
    )
    return ManagedMcpResponse.model_validate(row)
