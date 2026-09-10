from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.capabilities.mcp_observability import (
    McpToolCallLogRequest,
    McpToolCallLogResponse,
)
from backend.app.api.schemas.capabilities.policy_diagnostics import (
    AgentToolPolicyDiagnosticsResponse,
    WorkspaceToolPolicyMatrixResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.capabilities.mcp_call_logs import McpToolCallLogQueryService
from backend.app.capabilities.skill_tool_diagnostics import SkillToolDiagnosticsService
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


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
        diagnostics = SkillToolDiagnosticsService(session).agent_tool_policy_diagnostics(
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
    matrix = SkillToolDiagnosticsService(session).workspace_tool_policy_matrix(
        context.workspace.id
    )
    return WorkspaceToolPolicyMatrixResponse.model_validate(matrix)


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
        log = McpToolCallLogQueryService(session).log_mcp_tool_call(
            context.workspace.id,
            request,
        )
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
        items, total = McpToolCallLogQueryService(session).list_mcp_tool_call_logs(
            context.workspace.id,
            page,
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
            status=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)
