"""User binding HTTP contracts."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import CurrentUserDep
from opsmesh.identity.users.ownership import require_resource_owner
from opsmesh.orchestration.configurations.bindings import service
from opsmesh.orchestration.configurations.bindings.access import load_binding
from opsmesh.orchestration.configurations.bindings.connections import connect_binding
from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
from opsmesh.orchestration.configurations.bindings.schemas import (
    BindingConnect,
    BindingCreate,
    BindingUpdate,
    BindingView,
)
from opsmesh.orchestration.dependencies import scope_admission
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.dependencies import get_db_session

router = APIRouter(prefix="/orchestration/capability-bindings", tags=["orchestration"])
Db = Annotated[Session, Depends(get_db_session)]


@router.post(
    "",
    response_model=BindingView,
    status_code=201,
    operation_id="orchestration.capability_bindings.create",
)
def create(payload: BindingCreate, user: CurrentUserDep, session: Db) -> BindingView:
    with session_transaction(session):
        record = service.create_binding(
            session, actor=user, owner_user_id=user.user_id, request=payload
        )
    return BindingView.model_validate(record)


@router.post(
    "/{binding_id}/connection",
    response_model=BindingView,
    status_code=202,
    operation_id="orchestration.capability_bindings.connect",
)
def connect(
    payload: BindingConnect,
    binding_id: UUID,
    user: CurrentUserDep,
    session: Db,
    request: Request,
    idempotency_key: str = Query(min_length=1, max_length=100),
) -> BindingView:
    with session_transaction(session):
        record = connect_binding(
            session,
            actor=user,
            owner_user_id=user.user_id,
            binding_id=binding_id,
            request=payload,
            operation_id=idempotency_key,
            admission=scope_admission(request),
        )
    return BindingView.model_validate(record)


@router.get(
    "", response_model=list[BindingView], operation_id="orchestration.capability_bindings.list"
)
def list_bindings(
    user: CurrentUserDep,
    session: Db,
    limit: int = Query(default=100, ge=1, le=500),
    after: UUID | None = None,
) -> list[BindingView]:
    require_resource_owner(session, owner_user_id=user.user_id, user_id=user.user_id)
    query = select(CapabilityBinding).where(CapabilityBinding.owner_user_id == user.user_id)
    if after:
        query = query.where(CapabilityBinding.id > after)
    records = session.scalars(query.order_by(CapabilityBinding.id).limit(limit))
    return [
        BindingView.model_validate(
            load_binding(session, actor=user, owner_user_id=user.user_id, binding_id=record.id)[0]
        )
        for record in records
    ]


@router.get(
    "/{binding_id}",
    response_model=BindingView,
    operation_id="orchestration.capability_bindings.get",
)
def get(binding_id: UUID, user: CurrentUserDep, session: Db) -> BindingView:
    return BindingView.model_validate(
        load_binding(session, actor=user, owner_user_id=user.user_id, binding_id=binding_id)[0]
    )


@router.put(
    "/{binding_id}",
    response_model=BindingView,
    operation_id="orchestration.capability_bindings.update",
)
def update(
    payload: BindingUpdate, binding_id: UUID, user: CurrentUserDep, session: Db
) -> BindingView:
    with session_transaction(session):
        record = service.update_binding(
            session, actor=user, owner_user_id=user.user_id, binding_id=binding_id, request=payload
        )
    return BindingView.model_validate(record)
