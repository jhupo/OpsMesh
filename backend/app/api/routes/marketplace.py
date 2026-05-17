from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.marketplace import (
    HireTalentRequest,
    TalentListingCreateRequest,
    TalentListingResponse,
    TalentRecommendationRequest,
    TalentRecommendationResponse,
    WorkspaceAgentInstallResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.marketplace.service import TalentMarketplaceService

router = APIRouter(tags=["talent-marketplace"])


@router.get("/talent-market", response_model=PageResponse[TalentListingResponse])
async def list_talent_market(
    page: PageParams = Depends(pagination_params),
    query: str | None = Query(default=None),
    role: str | None = Query(default=None),
    skill: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[TalentListingResponse]:
    items, total = TalentMarketplaceService(session).list_public_listings(
        page,
        query=query,
        role=role,
        skill=skill,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/workspaces/{workspace_id}/talent-listings",
    response_model=TalentListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def publish_agent_to_talent_market(
    request: TalentListingCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TalentListingResponse:
    try:
        listing = TalentMarketplaceService(session).publish_agent(
            workspace_id=context.workspace.id,
            owner_user_id=context.user.user_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TalentListingResponse.model_validate(listing)


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
        install = TalentMarketplaceService(session).hire_agent(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            listing_id=listing_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WorkspaceAgentInstallResponse.model_validate(
        {
            "id": install.id,
            "created_at": install.created_at,
            "updated_at": install.updated_at,
            "workspace_id": install.workspace_id,
            "talent_listing_id": install.talent_listing_id,
            "source_agent_profile_id": install.source_agent_profile_id,
            "installed_agent_profile_id": install.installed_agent_profile_id,
            "hired_by_user_id": install.hired_by_user_id,
            "status": install.status,
            "agent": install.installed_agent_profile,
        }
    )


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
        return TalentMarketplaceService(session).recommend_team(
            workspace_id=context.workspace.id,
            data=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
