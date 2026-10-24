from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from opsmesh_sdk import UserIdentity
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http import get_application, get_current_user, get_db_session, serialize_record
from opsmesh.users.service import identity, require_admin, require_owner

from .models import ModelConnection, TokenBudget, TokenReservation
from .schemas import (
    CreateModelConnectionRequest,
    ModelConnectionView,
    QuotaLimitsView,
    SettleTokenReservationRequest,
    TokenReservationView,
    UpdateModelConnectionRequest,
    UpdateQuotaRequest,
    UserQuotaView,
)
from .storage import size
from .tokens import quota, settle

router = APIRouter(tags=["quotas"])


@router.get("/quotas/{user_id}", response_model=UserQuotaView)
def limits(
    user_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_owner(user, user_id)
    identity(db, user_id)
    return {
        "limits": serialize_record(quota(db, user_id)),
        "storage_used_bytes": size(get_application(request).settings.data_dir / "users" / user_id),
        "budgets": [
            serialize_record(row)
            for row in db.scalars(select(TokenBudget).where(TokenBudget.user_id == user_id))
        ],
    }


@router.put("/quotas/{user_id}", response_model=QuotaLimitsView)
def update_quota(
    user_id: str,
    body: UpdateQuotaRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    identity(db, user_id)
    row = quota(db, user_id)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    return serialize_record(row)


@router.get("/quotas/{user_id}/reservations", response_model=list[TokenReservationView])
def reservations(
    user_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    require_owner(user, user_id)
    return [
        serialize_record(row)
        for row in db.scalars(
            select(TokenReservation)
            .where(TokenReservation.user_id == user_id, TokenReservation.status == "reserved")
            .limit(200)
        )
    ]


@router.post("/quotas/reservations/{reservation_id}/settle", status_code=204)
def settle_reservation(
    reservation_id: str,
    body: SettleTokenReservationRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    require_admin(user)
    settle(db, reservation_id, body.actual)


@router.post("/connections", status_code=201, response_model=ModelConnectionView)
def create_connection(
    body: CreateModelConnectionRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    app = get_application(request)
    if urlsplit(body.base_url).hostname not in app.settings.allowed_model_hosts:
        raise ValueError("Model endpoint host is not enabled by the administrator")
    quota(db, user.user_id)
    if body.is_default:
        db.execute(
            update(ModelConnection)
            .where(ModelConnection.owner_id == user.user_id)
            .values(is_default=False)
        )
    row = ModelConnection(
        owner_id=user.user_id,
        name=body.name,
        base_url=body.base_url,
        api_mode=body.api_mode,
        model_name=body.model_name,
        reasoning_effort=body.reasoning_effort,
        is_default=body.is_default,
        encrypted_key=app.cipher.encrypt(body.api_key.get_secret_value()),
    )
    db.add(row)
    db.flush()
    return serialize_record(row)


@router.get("/connections/{connection_id}", response_model=ModelConnectionView)
def get_connection(
    connection_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    row = db.get(ModelConnection, connection_id)
    if row is None:
        raise NotFoundError("Connection not found")
    require_owner(user, row.owner_id)
    return serialize_record(row)


@router.patch("/connections/{connection_id}", response_model=ModelConnectionView)
def update_connection(
    connection_id: str,
    body: UpdateModelConnectionRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    row = db.get(ModelConnection, connection_id)
    if row is None:
        raise NotFoundError("Connection not found")
    require_owner(user, row.owner_id)
    quota(db, row.owner_id)
    row = db.get(ModelConnection, connection_id, with_for_update=True, populate_existing=True)
    if row is None:
        raise NotFoundError("Connection not found")
    app = get_application(request)
    changes = body.model_dump(exclude_unset=True, exclude={"api_key"})
    if any(value is None for value in changes.values()):
        raise ValueError("Model configuration fields cannot be null")
    if body.base_url and urlsplit(body.base_url).hostname not in app.settings.allowed_model_hosts:
        raise ValueError("Model endpoint host is not enabled by the administrator")
    if body.is_default:
        db.execute(
            update(ModelConnection)
            .where(ModelConnection.owner_id == row.owner_id)
            .values(is_default=False)
        )
    for key, value in changes.items():
        setattr(row, key, value)
    if not row.enabled:
        row.is_default = False
    if body.api_key is not None:
        row.encrypted_key = app.cipher.encrypt(body.api_key.get_secret_value())
    db.flush()
    return serialize_record(row)


@router.get("/connections", response_model=list[ModelConnectionView])
def connections(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    return [
        serialize_record(row)
        for row in db.scalars(
            select(ModelConnection).where(ModelConnection.owner_id == user.user_id).limit(200)
        )
    ]


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(
    connection_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    row = db.get(ModelConnection, connection_id)
    if row is None:
        raise NotFoundError("Connection not found")
    require_owner(user, row.owner_id)
    quota(db, row.owner_id)
    row = db.get(ModelConnection, connection_id, with_for_update=True, populate_existing=True)
    if row is None:
        raise NotFoundError("Connection not found")
    db.delete(row)
