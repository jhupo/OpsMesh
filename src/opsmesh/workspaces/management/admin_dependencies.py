from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.shared.db.session import get_db_session
from opsmesh.workspaces.management.admin_service import AdminWorkspaceManagementService


def admin_workspace_management_service(
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceManagementService:
    return AdminWorkspaceManagementService(session)
