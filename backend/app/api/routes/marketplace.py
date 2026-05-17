from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.marketplace import (
    HireTalentRequest,
    TalentInstallPinRequest,
    TalentInstallUpgradeRequest,
    TalentListingCreateRequest,
    TalentListingResponse,
    TalentRecommendationRequest,
    TalentRecommendationResponse,
    TalentUpgradeStatusResponse,
    WorkspaceAgentInstallResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.marketplace.service import TalentMarketplaceService, _install_response

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
