"""Workspace administrators manage declared remote channel instances."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.messaging.channels.models import ChannelConnection
from opsmesh.messaging.channels.schemas import (
    ChannelConnectionCreateRequest,
    ChannelConnectionResponse,
    ChannelConnectionUpdateRequest,
)
from opsmesh.messaging.channels.service import ChannelService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.errors import commit_or_raise_conflict
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.security.secrets import SecretEncryptionService

router = APIRouter(prefix="/workspaces/{workspace_id}/channels", tags=["channels"])


def _secrets(settings: Settings) -> SecretEncryptionService:
    return SecretEncryptionService(
        secret=settings.credential_encryption_secret,
        key_id=settings.credential_encryption_key_id,
        previous_secrets=settings.credential_encryption_previous_secrets,
    )


@router.get("", response_model=list[ChannelConnectionResponse])
def connections(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> list[ChannelConnection]:
    return list(
        session.scalars(
            select(ChannelConnection)
            .where(
                ChannelConnection.workspace_id == context.workspace.id,
            )
            .order_by(ChannelConnection.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.post("", response_model=ChannelConnectionResponse, status_code=201)
def create_connection(
    request: ChannelConnectionCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ChannelConnection:
    try:
        connection = ChannelService(session).create(
            context.workspace.id, context.user.user_id, request, _secrets(settings)
        )
    except ValueError as exc:
        raise HTTPException(422, "Invalid channel configuration") from exc
    commit_or_raise_conflict(session, "Channel connection name already exists")
    return connection


@router.put("/{connection_id}", response_model=ChannelConnectionResponse)
def update_connection(
    connection_id: UUID,
    request: ChannelConnectionUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ChannelConnection:
    try:
        connection = ChannelService(session).update(
            context.workspace.id, connection_id, context.user.user_id, request, _secrets(settings)
        )
    except ValueError as exc:
        raise HTTPException(422, "Invalid channel configuration") from exc
    commit_or_raise_conflict(session, "Channel connection name already exists")
    return connection
