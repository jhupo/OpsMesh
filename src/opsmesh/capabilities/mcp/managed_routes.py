from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.managed_schemas import (
    ManagedMCPActionRequest,
    ManagedMCPCreateRequest,
    ManagedMCPEnvironmentRequest,
    ManagedMCPResponse,
)
from opsmesh.capabilities.mcp.managed_service import ManagedMCPService
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.errors import DatabaseConflictError
from opsmesh.shared.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.put("/mcp-servers/{server_id}/deployment/environment", response_model=ManagedMCPResponse)
def bind_managed_mcp_environment(
    server_id: UUID,
    request: ManagedMCPEnvironmentRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ManagedMCPResponse:
    row = ManagedMCPService(session, settings).bind_environment(
        context.workspace.id,
        server_id,
        context.user,
        request.runtime_id,
    )
    return ManagedMCPResponse.model_validate(row)


@router.post("/managed-mcp", response_model=list[ManagedMCPResponse], status_code=202)
def create_managed_mcp(
    request: ManagedMCPCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[ManagedMCPResponse]:
    try:
        rows = ManagedMCPService(session, settings).create(
            context.workspace.id, context.user, request
        )
    except DatabaseConflictError as exc:
        raise HTTPException(409, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [ManagedMCPResponse.model_validate(row) for row in rows]


@router.get("/mcp-servers/{server_id}/deployment", response_model=ManagedMCPResponse)
def get_managed_mcp(
    server_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ManagedMCPResponse:
    row = ManagedMCPService(session, settings).get(context.workspace.id, server_id)
    return ManagedMCPResponse.model_validate(row)


@router.post(
    "/mcp-servers/{server_id}/deployment", response_model=ManagedMCPResponse, status_code=202
)
def control_managed_mcp(
    server_id: UUID,
    request: ManagedMCPActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ManagedMCPResponse:
    row = ManagedMCPService(session, settings).control(
        context.workspace.id, server_id, context.user, request.action
    )
    return ManagedMCPResponse.model_validate(row)
