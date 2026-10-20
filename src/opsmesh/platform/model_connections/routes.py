"""Administrator upstream management and users' authorized model catalogue."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.platform.model_connections import service
from opsmesh.platform.model_connections.models import ModelProviderConnection
from opsmesh.platform.model_connections.schemas import (
    AvailableModelConnectionView,
    ModelConnectionCreateRequest,
    ModelConnectionPage,
    ModelConnectionUpdateRequest,
    ModelConnectionView,
    ModelGrantRequest,
    ModelGrantView,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.pagination import pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.encryption import SecretCipher

router = APIRouter(prefix="/platform/model-connections", tags=["platform"])
Pagination = Annotated[PageParams, Depends(pagination_params)]


def _cipher(request: Request) -> SecretCipher:
    cipher = getattr(request.app.state, "model_key_cipher", None)
    if not isinstance(cipher, SecretCipher):
        raise HTTPException(503, "Model key encryption is not assembled")
    return cipher


def _view(connection: ModelProviderConnection) -> ModelConnectionView:
    return ModelConnectionView(
        connection_id=connection.id,
        revision=connection.revision,
        name=connection.name,
        provider=connection.provider,
        base_url=connection.base_url,
        default_model=connection.default_model,
        models=connection.models,
        status=connection.status,
        last_probe_status=connection.last_probe_status,
        last_probe_detail=connection.last_probe_detail,
        last_probe_at=connection.last_probe_at,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


@router.get("/available", operation_id="platform.model_connections.available")
def available_connections(
    session: DbSession, user: CurrentUserDep
) -> list[AvailableModelConnectionView]:
    return [
        AvailableModelConnectionView(
            connection_id=connection.id,
            revision=connection.revision,
            name=connection.name,
            provider=connection.provider,
            models=[model for model in grant.models if model in connection.models],
            default_model=(
                connection.default_model
                if connection.default_model in grant.models
                else next(model for model in grant.models if model in connection.models)
            ),
            is_default=grant.is_default,
        )
        for connection, grant in service.available_connections(session, actor=user)
        if set(connection.models) & set(grant.models)
    ]


@router.get("", operation_id="platform.model_connections.list")
def list_connections(
    session: DbSession, user: CurrentUserDep, page: Pagination
) -> ModelConnectionPage:
    connections, total = service.list_connections(session, actor=user, page=page)
    return ModelConnectionPage(connections=[_view(item) for item in connections], total=total)


@router.post("", status_code=201, operation_id="platform.model_connections.create")
def create_connection(
    session: DbSession,
    user: CurrentUserDep,
    payload: ModelConnectionCreateRequest,
    request: Request,
) -> ModelConnectionView:
    with session_transaction(session):
        result = service.create_connection(
            session,
            actor=user,
            cipher=_cipher(request),
            name=payload.name,
            api_key=payload.api_key.get_secret_value(),
            base_url=payload.base_url,
            default_model=payload.default_model,
            models=payload.models,
        )
    return _view(result)


@router.get("/{connection_id}", operation_id="platform.model_connections.read")
def get_connection(
    session: DbSession, user: CurrentUserDep, connection_id: UUID
) -> ModelConnectionView:
    return _view(service.get_connection(session, actor=user, connection_id=connection_id))


@router.patch("/{connection_id}", operation_id="platform.model_connections.update")
def update_connection(
    session: DbSession,
    user: CurrentUserDep,
    connection_id: UUID,
    payload: ModelConnectionUpdateRequest,
    request: Request,
) -> ModelConnectionView:
    with session_transaction(session):
        result = service.update_connection(
            session,
            actor=user,
            cipher=_cipher(request),
            connection_id=connection_id,
            expected_revision=payload.expected_revision,
            name=payload.name,
            api_key=payload.api_key.get_secret_value() if payload.api_key else None,
            base_url=payload.base_url,
            default_model=payload.default_model,
            models=payload.models,
        )
    return _view(result)


@router.delete("/{connection_id}", operation_id="platform.model_connections.disable")
def disable_connection(
    session: DbSession, user: CurrentUserDep, connection_id: UUID
) -> ModelConnectionView:
    with session_transaction(session):
        result = service.disable_connection(session, actor=user, connection_id=connection_id)
    return _view(result)


@router.put("/{connection_id}/users/{user_id}", operation_id="platform.model_connections.grant")
def grant_connection(
    session: DbSession,
    user: CurrentUserDep,
    connection_id: UUID,
    user_id: UUID,
    payload: ModelGrantRequest,
) -> ModelGrantView:
    with session_transaction(session):
        grant = service.grant_connection(
            session,
            actor=user,
            connection_id=connection_id,
            user_id=user_id,
            models=payload.models,
            is_default=payload.is_default,
        )
        result = ModelGrantView(
            connection_id=grant.connection_id,
            user_id=grant.user_id,
            models=grant.models,
            is_default=grant.is_default,
        )
    return result


@router.delete(
    "/{connection_id}/users/{user_id}",
    status_code=204,
    operation_id="platform.model_connections.revoke",
)
def revoke_connection(
    session: DbSession,
    user: CurrentUserDep,
    connection_id: UUID,
    user_id: UUID,
) -> Response:
    with session_transaction(session):
        service.revoke_connection(session, actor=user, connection_id=connection_id, user_id=user_id)
    return Response(status_code=204)


@router.post(
    "/{connection_id}/probe", status_code=202, operation_id="platform.model_connections.probe"
)
def start_probe(
    session: DbSession, user: CurrentUserDep, connection_id: UUID
) -> ModelConnectionView:
    with session_transaction(session):
        result = service.start_probe(session, actor=user, connection_id=connection_id)
    return _view(result)
