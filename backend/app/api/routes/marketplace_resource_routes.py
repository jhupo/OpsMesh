from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.marketplace import (
    MarketplaceInstallRequest,
    MarketplaceListingCreateRequest,
    MarketplaceListingResponse,
    MarketplaceListingType,
    WorkspaceMarketplaceInstallResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.marketplace.resource_service import MarketplaceService
from backend.app.marketplace.responses import marketplace_install_response

router = APIRouter()


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
