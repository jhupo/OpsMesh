"""User quota query and platform limit configuration APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.quotas import service
from opsmesh.identity.quotas.schemas import QuotaLimitsRequest, QuotaView
from opsmesh.shared.db.session import session_transaction

router = APIRouter(tags=["identity"])


def _view(snapshot: service.QuotaSnapshot) -> QuotaView:
    return QuotaView(
        token_limit=snapshot.token_limit,
        used_tokens=snapshot.used_tokens,
        reserved_tokens=snapshot.reserved_tokens,
        available_tokens=snapshot.available_tokens,
        storage_limit_bytes=snapshot.storage_limit_bytes,
        used_bytes=snapshot.used_bytes,
        reserved_bytes=snapshot.reserved_bytes,
        available_bytes=snapshot.available_bytes,
    )


def _readable_user_id(user_id: UUID, reader: CurrentUser) -> UUID:
    """An account reads its own quota; anything else needs the admin role."""
    if reader.user_id != user_id:
        require_platform_admin(reader)
    return user_id


@router.get(
    "/users/me/quotas", response_model=QuotaView, operation_id="identity.quotas.read_current"
)
def read_current_quota(session: DbSession, user: CurrentUserDep) -> QuotaView:
    return _view(service.get_quota(session, user.user_id))


@router.get(
    "/users/{user_id}/quotas", response_model=QuotaView, operation_id="identity.quotas.read"
)
def read_user_quota(user_id: UUID, session: DbSession, user: CurrentUserDep) -> QuotaView:
    return _view(service.get_quota(session, _readable_user_id(user_id, user)))


@router.put(
    "/users/{user_id}/quotas", response_model=QuotaView, operation_id="identity.quotas.update"
)
def update_user_quota(
    user_id: UUID,
    payload: QuotaLimitsRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> QuotaView:
    require_platform_admin(user)
    with session_transaction(session):
        service.set_limits(
            session,
            user_id=user_id,
            actor=AuditActor.from_user(user),
            token_limit=payload.token_limit,
            storage_limit_bytes=payload.storage_limit_bytes,
        )
        snapshot = service.get_quota(session, user_id)
    return _view(snapshot)
