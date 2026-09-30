from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.schemas.platform.admin import (
    AdminPluginGovernanceRequest,
    AdminPluginGovernanceResponse,
    AdminPluginPublisherTrustResponse,
)
from backend.app.capabilities.plugins.service import PluginService
from backend.app.core.db.session import get_db_session

router = APIRouter()


@router.post(
    "/workspaces/{workspace_id}/plugins/{install_id}/disable",
    response_model=AdminPluginGovernanceResponse,
)
def disable_plugin(
    workspace_id: UUID,
    install_id: UUID,
    request: AdminPluginGovernanceRequest,
    session: Session = Depends(get_db_session),
) -> object:
    return PluginService(session).platform_disable(workspace_id, install_id, reason=request.reason)


@router.post(
    "/workspaces/{workspace_id}/plugins/{install_id}/release",
    response_model=AdminPluginGovernanceResponse,
)
def release_plugin(
    workspace_id: UUID,
    install_id: UUID,
    request: AdminPluginGovernanceRequest,
    session: Session = Depends(get_db_session),
) -> object:
    return PluginService(session).platform_release(workspace_id, install_id, reason=request.reason)


@router.post(
    "/workspaces/{workspace_id}/plugin-trust-keys/{key_id}/revoke",
    response_model=AdminPluginPublisherTrustResponse,
)
def revoke_publisher_key(
    workspace_id: UUID,
    key_id: UUID,
    request: AdminPluginGovernanceRequest,
    session: Session = Depends(get_db_session),
) -> object:
    return PluginService(session).platform_revoke_key(workspace_id, key_id, reason=request.reason)
