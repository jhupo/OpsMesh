from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.capabilities.governance.diagnostic_schemas import (
    AgentToolPolicyDiagnosticsResponse,
    WorkspaceToolPolicyMatrixResponse,
)
from opsmesh.capabilities.mcp.execution.contracts import (
    McpToolCallLogRequest,
    McpToolCallLogResponse,
)
from opsmesh.capabilities.mcp.execution.events import McpToolCallLogQueryService
from opsmesh.capabilities.skills.diagnostics import SkillToolDiagnosticsService
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.get(
    "/agents/{agent_profile_id}/tool-policy-diagnostics",
    response_model=AgentToolPolicyDiagnosticsResponse,
)
def get_agent_tool_policy_diagnostics(
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
def get_workspace_tool_policy_matrix(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceToolPolicyMatrixResponse:
    matrix = SkillToolDiagnosticsService(session).workspace_tool_policy_matrix(context.workspace.id)
    return WorkspaceToolPolicyMatrixResponse.model_validate(matrix)


@router.post(
    "/mcp-tool-call-logs",
    response_model=McpToolCallLogResponse,
    status_code=status.HTTP_201_CREATED,
)
def log_mcp_tool_call(
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
def list_mcp_tool_call_logs(
    page: PageParams = Depends(pagination_params),
    mcp_server_id: UUID | None = Query(default=None),
    tool_name: str | None = Query(default=None, min_length=1, max_length=160),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    trace_id: str | None = Query(default=None, min_length=32, max_length=32),
    request_id: str | None = Query(default=None, min_length=1, max_length=80),
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
            trace_id=trace_id,
            request_id=request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)
