from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.marketplace import (
    TalentListingCreateRequest,
    TalentListingResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.marketplace.talent_publish import TalentPublishService

router = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/talent-listings",
    response_model=TalentListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def publish_agent_to_talent_market(
    request: TalentListingCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TalentListingResponse:
    try:
        listing = TalentPublishService(session, settings=settings).publish_agent(
            workspace_id=context.workspace.id,
            owner_user_id=context.user.user_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TalentListingResponse.model_validate(listing)
