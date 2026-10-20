"""Administrator API for explicit, whitelisted platform commands."""

from uuid import UUID

from fastapi import APIRouter, Request

from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.platform.operations import service
from opsmesh.platform.operations.admission import is_open
from opsmesh.platform.operations.models import PlatformOperation
from opsmesh.platform.operations.schemas import (
    PlatformAdmissionView,
    PlatformOperationCreate,
    PlatformOperationView,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError

router = APIRouter(prefix="/platform/operations", tags=["platform"])


@router.post(
    "",
    response_model=PlatformOperationView,
    status_code=202,
    operation_id="platform.operations.submit",
)
def create(
    payload: PlatformOperationCreate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> PlatformOperationView:
    require_platform_admin(user)
    with session_transaction(session):
        operation = service.submit(
            session,
            owner=payload.owner,
            command=payload.command,
            target=payload.target,
            idempotency_key=payload.idempotency_key,
            actor=user,
            handlers=getattr(request.app.state, "platform_operation_handlers", {}),
        )
    return PlatformOperationView.model_validate(operation, from_attributes=True)


@router.post(
    "/{operation_id}/cancel",
    response_model=PlatformOperationView,
    operation_id="platform.operations.cancel",
)
def cancel(operation_id: UUID, session: DbSession, user: CurrentUserDep) -> PlatformOperationView:
    with session_transaction(session):
        operation = service.cancel(session, operation_id=operation_id, actor=user)
    return PlatformOperationView.model_validate(operation, from_attributes=True)


@router.get(
    "/admission", response_model=PlatformAdmissionView, operation_id="platform.operations.admission"
)
def admission(session: DbSession, user: CurrentUserDep, request: Request) -> PlatformAdmissionView:
    require_platform_admin(user)
    return PlatformAdmissionView(
        drained=not is_open(session), active=request.app.state.platform_active_work(session)
    )


@router.post(
    "/{operation_id}/resume",
    status_code=202,
    response_model=PlatformOperationView,
    operation_id="platform.operations.resume",
)
def resume(
    operation_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> PlatformOperationView:
    with session_transaction(session):
        operation = service.resume(
            session,
            operation_id=operation_id,
            actor=user,
            handlers=request.app.state.platform_operation_handlers,
        )
    return PlatformOperationView.model_validate(operation)


@router.get(
    "/{operation_id}",
    response_model=PlatformOperationView,
    operation_id="platform.operations.get",
)
def get(operation_id: UUID, session: DbSession, user: CurrentUserDep) -> PlatformOperationView:
    require_platform_admin(user)
    operation = session.get(PlatformOperation, operation_id)
    if operation is None:
        raise NotFoundError("Platform operation not found")
    return PlatformOperationView.model_validate(operation, from_attributes=True)
