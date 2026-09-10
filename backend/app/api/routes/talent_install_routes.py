from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.marketplace import (
    TalentInstallPinRequest,
    TalentInstallUpgradeRequest,
    TalentUpgradeStatusResponse,
    WorkspaceAgentInstallResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session
from backend.app.marketplace.responses import install_response
from backend.app.marketplace.talent_catalog import TalentCatalogService
from backend.app.marketplace.talent_upgrades import TalentInstallUpgradeService

router = APIRouter()


@router.get(
    "/workspaces/{workspace_id}/talent-installs",
    response_model=PageResponse[WorkspaceAgentInstallResponse],
)
async def list_workspace_talent_installs(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceAgentInstallResponse]:
    items, total = TalentCatalogService(session).list_installs(context.workspace.id, page)
    return PageResponse(
        items=[install_response(item) for item in items],
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
    response = TalentInstallUpgradeService(session).get_upgrade_status(
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
    install = TalentInstallUpgradeService(session).set_install_pin(
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
    return install_response(install)


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
        install = TalentInstallUpgradeService(session).upgrade_install(
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
    return install_response(install)
