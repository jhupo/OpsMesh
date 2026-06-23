from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.marketplace import (
    HireTalentRequest,
    HireTaskTalentRequest,
    WorkspaceAgentInstallResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.marketplace.responses import install_response
from backend.app.marketplace.talent_hiring import TalentHiringService

router = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/talent-market/{listing_id}/hire",
    response_model=WorkspaceAgentInstallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def hire_agent_from_talent_market(
    listing_id: UUID,
    request: HireTalentRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceAgentInstallResponse:
    try:
        install = TalentHiringService(session).hire_agent(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            listing_id=listing_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return install_response(install)


@router.post(
    "/workspaces/{workspace_id}/tasks/{task_id}/talent-market/hire",
    response_model=WorkspaceAgentInstallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def hire_talent_for_task_staffing_gap(
    task_id: UUID,
    request: HireTaskTalentRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceAgentInstallResponse:
    try:
        install = TalentHiringService(session).hire_for_task_staffing_gap(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            task_id=task_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if install is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return install_response(install)
