"""Model provider connection APIs: lifecycle, default resolution and probes.

Starting a probe stages the durable execution and returns immediately; the
probe state is read back from the connection. The resolved default model
carries no secret: execution decrypts the referenced credential inside its
own activity when it actually calls the provider.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.workspaces.model_providers import service
from opsmesh.workspaces.model_providers.models import ModelProviderConnection
from opsmesh.workspaces.model_providers.schemas import (
    DefaultModelView,
    ModelProviderCreateRequest,
    ModelProviderPage,
    ModelProviderUpdateRequest,
    ModelProviderView,
    ProbeView,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/model-providers", tags=["workspaces"]
)

WorkspaceIdPath = Annotated[UUID, Path()]
ConnectionIdPath = Annotated[UUID, Path()]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _view(connection: ModelProviderConnection) -> ModelProviderView:
    return ModelProviderView(
        connection_id=connection.id,
        workspace_id=connection.workspace_id,
        credential_id=connection.credential_id,
        name=connection.name,
        provider=connection.provider,
        base_url=connection.base_url,
        default_model=connection.default_model,
        models=list(connection.models),
        is_default=connection.is_default,
        status=connection.status,
        last_probe_status=connection.last_probe_status,
        last_probe_detail=connection.last_probe_detail,
        last_probe_at=connection.last_probe_at,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _probe_view(connection: ModelProviderConnection) -> ProbeView:
    return ProbeView(
        connection_id=connection.id,
        status=connection.last_probe_status,
        detail=connection.last_probe_detail,
        probed_at=connection.last_probe_at,
    )


@router.post(
    "",
    response_model=ModelProviderView,
    status_code=status.HTTP_201_CREATED,
    operation_id="workspaces.model_providers.create",
)
def create_connection(
    payload: ModelProviderCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
) -> ModelProviderView:
    """Register one connection over an existing active provider credential."""
    with session_transaction(session):
        connection = service.create_connection(
            session,
            actor=user,
            workspace_id=workspace_id,
            name=payload.name,
            credential_id=payload.credential_id,
            base_url=payload.base_url,
            default_model=payload.default_model,
            models=payload.models,
            is_default=payload.is_default,
        )
    return _view(connection)


@router.get("", response_model=ModelProviderPage, operation_id="workspaces.model_providers.list")
def list_connections(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> ModelProviderPage:
    """List the workspace's model provider connections."""
    connections, total = service.list_connections(
        session, actor=user, workspace_id=workspace_id, page=PageParams(limit=limit, offset=offset)
    )
    return ModelProviderPage(connections=[_view(item) for item in connections], total=total)


@router.get(
    "/default",
    response_model=DefaultModelView,
    operation_id="workspaces.model_providers.read_default",
)
def read_default_model(
    session: DbSession, user: CurrentUserDep, workspace_id: WorkspaceIdPath
) -> DefaultModelView:
    """Resolve the workspace's default model; a missing default is a 404."""
    connection = service.resolve_default_model(session, actor=user, workspace_id=workspace_id)
    if connection is None:
        raise NotFoundError("The workspace has no default model connection")
    return DefaultModelView(
        connection_id=connection.id,
        provider=connection.provider,
        default_model=connection.default_model,
        base_url=connection.base_url,
    )


@router.get(
    "/{connection_id}",
    response_model=ModelProviderView,
    operation_id="workspaces.model_providers.read",
)
def read_connection(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    connection_id: ConnectionIdPath,
) -> ModelProviderView:
    return _view(
        service.get_connection(
            session, actor=user, workspace_id=workspace_id, connection_id=connection_id
        )
    )


@router.patch(
    "/{connection_id}",
    response_model=ModelProviderView,
    operation_id="workspaces.model_providers.update",
)
def update_connection(
    payload: ModelProviderUpdateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    connection_id: ConnectionIdPath,
) -> ModelProviderView:
    """Change one connection's catalog, endpoint or default selection."""
    with session_transaction(session):
        connection = service.update_connection(
            session,
            actor=user,
            workspace_id=workspace_id,
            connection_id=connection_id,
            name=payload.name,
            base_url=payload.base_url,
            default_model=payload.default_model,
            models=payload.models,
            is_default=payload.is_default,
        )
    return _view(connection)


@router.post(
    "/{connection_id}/default",
    response_model=ModelProviderView,
    operation_id="workspaces.model_providers.set_default",
)
def set_default_connection(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    connection_id: ConnectionIdPath,
) -> ModelProviderView:
    """Make one connection the workspace default."""
    with session_transaction(session):
        connection = service.set_default_connection(
            session, actor=user, workspace_id=workspace_id, connection_id=connection_id
        )
    return _view(connection)


@router.post(
    "/{connection_id}/probe",
    response_model=ProbeView,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="workspaces.model_providers.probe",
)
def start_probe(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    connection_id: ConnectionIdPath,
) -> ProbeView:
    """Start one durable connection probe; the result is read back later."""
    with session_transaction(session):
        connection = service.start_probe(
            session, actor=user, workspace_id=workspace_id, connection_id=connection_id
        )
    return _probe_view(connection)


@router.get(
    "/{connection_id}/probe",
    response_model=ProbeView,
    operation_id="workspaces.model_providers.read_probe",
)
def read_probe(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    connection_id: ConnectionIdPath,
) -> ProbeView:
    """Read the latest probe state of one connection."""
    return _probe_view(
        service.read_probe(
            session, actor=user, workspace_id=workspace_id, connection_id=connection_id
        )
    )


@router.delete(
    "/{connection_id}",
    response_model=ModelProviderView,
    operation_id="workspaces.model_providers.disable",
)
def disable_connection(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    connection_id: ConnectionIdPath,
) -> ModelProviderView:
    """Disable one connection; a disabled default steps down with it."""
    with session_transaction(session):
        connection = service.disable_connection(
            session, actor=user, workspace_id=workspace_id, connection_id=connection_id
        )
    return _view(connection)
