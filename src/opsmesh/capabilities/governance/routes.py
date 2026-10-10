from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.capabilities.governance.schemas import (
    WorkspaceCapabilityGovernanceApplyRequest,
    WorkspaceCapabilityGovernanceApplyResponse,
    WorkspaceCapabilityGovernanceResponse,
)
from opsmesh.capabilities.governance.service import CapabilityGovernanceService
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.db.session import get_db_session

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.get("/governance", response_model=WorkspaceCapabilityGovernanceResponse)
def get_workspace_capability_governance(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCapabilityGovernanceResponse:
    diagnostics = CapabilityGovernanceService(session).workspace_capability_governance(
        context.workspace.id,
    )
    return WorkspaceCapabilityGovernanceResponse.model_validate(diagnostics)


@router.post(
    "/governance/actions/apply",
    response_model=WorkspaceCapabilityGovernanceApplyResponse,
)
def apply_workspace_capability_governance_actions(
    request: WorkspaceCapabilityGovernanceApplyRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCapabilityGovernanceApplyResponse:
    try:
        response = CapabilityGovernanceService(
            session,
        ).apply_workspace_capability_governance_actions(
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
