from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.shared.db.session import get_db_session
from opsmesh.workspaces.management.admin_schemas import (
    AdminWorkspaceResponse,
    AdminWorkspaceStatusUpdateRequest,
)
from opsmesh.workspaces.management.admin_service import AdminWorkspaceManagementService
from opsmesh.workspaces.management.models import Workspace

router = APIRouter(dependencies=[Depends(require_platform_admin)])


def _workspace_response(
    workspace: Workspace,
    *,
    member_count: int,
    project_count: int,
) -> AdminWorkspaceResponse:
    return AdminWorkspaceResponse(
        id=workspace.id,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        owner_user_id=workspace.owner_user_id,
        name=workspace.name,
        slug=workspace.slug,
        status=workspace.status,
        settings=workspace.settings,
        member_count=member_count,
        project_count=project_count,
    )


@router.get(
    "/workspaces/{workspace_id}",
    response_model=AdminWorkspaceResponse,
)
def get_admin_workspace(
    workspace_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceResponse:
    service = AdminWorkspaceManagementService(session)
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    member_count, project_count = service.workspace_counts(workspace.id)
    return _workspace_response(
        workspace,
        member_count=member_count,
        project_count=project_count,
    )


@router.patch(
    "/workspaces/{workspace_id}/status",
    response_model=AdminWorkspaceResponse,
)
def update_admin_workspace_status(
    workspace_id: UUID,
    request: AdminWorkspaceStatusUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceResponse:
    service = AdminWorkspaceManagementService(session)
    workspace = service.update_workspace_status(
        workspace_id,
        status=request.status,
        reason=request.reason,
    )
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    member_count, project_count = service.workspace_counts(workspace.id)
    return _workspace_response(
        workspace,
        member_count=member_count,
        project_count=project_count,
    )
