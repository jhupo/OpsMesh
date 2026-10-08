from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.shared.db.session import get_db_session
from backend.app.workspaces.management.admin_service import AdminWorkspaceManagementService

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object





def admin_workspace_management_service(
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceManagementService:
    return AdminWorkspaceManagementService(session)
