from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.marketplace import (
    TalentRecommendationRequest,
    TalentRecommendationResponse,
    TaskTalentRecommendationResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session
from backend.app.marketplace.talent_recommendations import TalentRecommendationService

router = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/talent-market/recommendations",
    response_model=TalentRecommendationResponse,
)
async def recommend_talent_for_team(
    request: TalentRecommendationRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TalentRecommendationResponse:
    try:
        return TalentRecommendationService(session).recommend_team(
            workspace_id=context.workspace.id,
            data=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/workspaces/{workspace_id}/tasks/{task_id}/talent-market/recommendations",
    response_model=TaskTalentRecommendationResponse,
)
async def recommend_talent_for_task_staffing(
    task_id: UUID,
    max_candidates_per_role: int = Query(default=3, ge=1, le=10),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskTalentRecommendationResponse:
    response = TalentRecommendationService(session).recommend_for_task_staffing(
        workspace_id=context.workspace.id,
        task_id=task_id,
        max_candidates_per_role=max_candidates_per_role,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return response
