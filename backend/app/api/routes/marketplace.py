from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.marketplace import (
    HireTalentRequest,
    HireTaskTalentRequest,
    MarketplaceInstallRequest,
    MarketplaceListingCreateRequest,
    MarketplaceListingResponse,
    MarketplaceListingType,
    TalentInstallPinRequest,
    TalentInstallUpgradeRequest,
    TalentListingCreateRequest,
    TalentListingMetricsResponse,
    TalentListingResponse,
    TalentListingReviewCreateRequest,
    TalentListingReviewResponse,
    TalentRecommendationRequest,
    TalentRecommendationResponse,
    TalentUpgradeStatusResponse,
    TaskTalentRecommendationResponse,
    WorkspaceAgentInstallResponse,
    WorkspaceMarketplaceInstallResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.marketplace.service import (
    MarketplaceService,
    TalentMarketplaceService,
    _install_response,
    marketplace_install_response,
    review_response,
)

router = APIRouter(tags=["talent-marketplace"])


@router.get("/marketplace", response_model=PageResponse[MarketplaceListingResponse])
async def list_public_marketplace_listings(
    listing_type: MarketplaceListingType = Query(),
    page: PageParams = Depends(pagination_params),
    query: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[MarketplaceListingResponse]:
    items, total = MarketplaceService(session).list_public_listings(
        page,
        listing_type=listing_type,
        query=query,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/workspaces/{workspace_id}/marketplace-listings",
    response_model=MarketplaceListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_marketplace_listing(
    request: MarketplaceListingCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MarketplaceListingResponse:
    try:
        listing = MarketplaceService(session, settings=settings).create_workspace_listing(
            workspace_id=context.workspace.id,
            owner_user_id=context.user.user_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MarketplaceListingResponse.model_validate(listing)


@router.post(
    "/workspaces/{workspace_id}/marketplace-listings/{listing_id}/install",
    response_model=WorkspaceMarketplaceInstallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def install_marketplace_listing(
    listing_id: UUID,
    request: MarketplaceInstallRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceMarketplaceInstallResponse:
    try:
        install = MarketplaceService(session).install_listing(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            listing_id=listing_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return marketplace_install_response(install)


@router.get(
    "/workspaces/{workspace_id}/marketplace-installs",
    response_model=PageResponse[WorkspaceMarketplaceInstallResponse],
)
async def list_workspace_marketplace_installs(
    page: PageParams = Depends(pagination_params),
    listing_type: MarketplaceListingType | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceMarketplaceInstallResponse]:
    items, total = MarketplaceService(session).list_workspace_installs(
        context.workspace.id,
        page,
        listing_type=listing_type,
    )
    return PageResponse(
        items=[marketplace_install_response(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


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


@router.get(
    "/talent-market/{listing_id}/metrics",
    response_model=TalentListingMetricsResponse,
)
async def get_talent_listing_metrics(
    listing_id: UUID,
    session: Session = Depends(get_db_session),
) -> TalentListingMetricsResponse:
    response = TalentMarketplaceService(session).listing_metrics(listing_id)
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Talent listing not found",
        )
    return response


@router.get(
    "/talent-market/{listing_id}/reviews",
    response_model=PageResponse[TalentListingReviewResponse],
)
async def list_talent_listing_reviews(
    listing_id: UUID,
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> PageResponse[TalentListingReviewResponse]:
    result = TalentMarketplaceService(session).list_reviews(listing_id, page)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Talent listing not found",
        )
    items, total = result
    return PageResponse(
        items=[review_response(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


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
        listing = TalentMarketplaceService(session, settings=settings).publish_agent(
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
    return _install_response(install)


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
    response = TalentMarketplaceService(session).recommend_for_task_staffing(
        workspace_id=context.workspace.id,
        task_id=task_id,
        max_candidates_per_role=max_candidates_per_role,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return response


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
        install = TalentMarketplaceService(session).hire_for_task_staffing_gap(
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
    return _install_response(install)


@router.get(
    "/workspaces/{workspace_id}/talent-installs",
    response_model=PageResponse[WorkspaceAgentInstallResponse],
)
async def list_workspace_talent_installs(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceAgentInstallResponse]:
    items, total = TalentMarketplaceService(session).list_installs(context.workspace.id, page)
    return PageResponse(
        items=[_install_response(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/workspaces/{workspace_id}/talent-installs/{install_id}/upgrade-status",
    response_model=TalentUpgradeStatusResponse,
)
async def get_talent_install_upgrade_status(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TalentUpgradeStatusResponse:
    response = TalentMarketplaceService(session).get_upgrade_status(
        workspace_id=context.workspace.id,
        install_id=install_id,
    )
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Talent install not found",
        )
    return response


@router.post(
    "/workspaces/{workspace_id}/talent-installs/{install_id}/pin",
    response_model=WorkspaceAgentInstallResponse,
)
async def update_talent_install_pin(
    install_id: UUID,
    request: TalentInstallPinRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceAgentInstallResponse:
    install = TalentMarketplaceService(session).set_install_pin(
        workspace_id=context.workspace.id,
        install_id=install_id,
        data=request,
        user_id=context.user.user_id,
    )
    if install is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Talent install not found",
        )
    return _install_response(install)


@router.post(
    "/workspaces/{workspace_id}/talent-installs/{install_id}/upgrade",
    response_model=WorkspaceAgentInstallResponse,
)
async def upgrade_talent_install(
    install_id: UUID,
    request: TalentInstallUpgradeRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> WorkspaceAgentInstallResponse:
    try:
        install = TalentMarketplaceService(session).upgrade_install(
            workspace_id=context.workspace.id,
            install_id=install_id,
            data=request,
            user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if install is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Talent install not found",
        )
    return _install_response(install)


@router.post(
    "/workspaces/{workspace_id}/talent-market/{listing_id}/reviews",
    response_model=TalentListingReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def review_talent_listing(
    listing_id: UUID,
    request: TalentListingReviewCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TalentListingReviewResponse:
    try:
        review = TalentMarketplaceService(session).upsert_review(
            workspace_id=context.workspace.id,
            user_id=context.user.user_id,
            listing_id=listing_id,
            data=request,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return review_response(review)
