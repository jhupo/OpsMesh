"""Public metered one-shot model operations, dispatched asynchronously."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
from opsmesh.orchestration.runs.model_operations import (
    ModelOperation,
    ModelOperationReconciliation,
    ModelOperationRequest,
    ModelOperations,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http.dependencies import get_db_session

router = APIRouter(prefix="/orchestration/model-operations", tags=["orchestration"])
DbSession = Annotated[Session, Depends(get_db_session)]


class ModelOperationView(BaseModel):
    id: UUID
    status: str
    revision: int
    result: dict[str, Any] | None = None
    usage: dict[str, Any]


def view(record: ModelOperation, request: Request) -> ModelOperationView:
    return ModelOperationView(
        id=record.id,
        status=record.status,
        revision=record.revision,
        usage=record.usage,
        result=request.app.state.secret_cipher.decrypt_payload(record.result_ciphertext)
        if record.result_ciphertext
        else None,
    )


@router.post(
    "",
    status_code=202,
    response_model=ModelOperationView,
    operation_id="orchestration.model_operations.create",
)
def create(
    payload: ModelOperationRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Annotated[str, Query(min_length=1, max_length=200)],
) -> ModelOperationView:
    with session_transaction(session):
        operations: ModelOperations = request.app.state.execution_model_operations
        return view(
            operations.submit(
                session, principal_id=user.user_id, request=payload, operation_id=idempotency_key
            ),
            request,
        )


@router.get(
    "/{operation_id}",
    response_model=ModelOperationView,
    operation_id="orchestration.model_operations.get",
)
def get(
    operation_id: UUID, user: CurrentUserDep, session: DbSession, request: Request
) -> ModelOperationView:
    record = session.get(ModelOperation, operation_id)
    if record is None or record.principal_id != user.user_id:
        raise NotFoundError("The model operation is unavailable")
    request.app.state.execution_scope_admission.admit(
        session,
        subject=ExecutionSubject(user.user_id),
        scope=ExecutionScope(
            configuration_id=record.configuration_id,
        ),
    )
    return view(record, request)


@router.post(
    "/{operation_id}/reconcile",
    response_model=ModelOperationView,
    operation_id="orchestration.model_operations.reconcile",
)
def reconcile(
    operation_id: UUID,
    payload: ModelOperationReconciliation,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> ModelOperationView:
    require_platform_admin(user)
    with session_transaction(session):
        record = session.get(ModelOperation, operation_id, with_for_update=True)
        if record is None:
            raise NotFoundError("The model operation is unavailable")
        operations: ModelOperations = request.app.state.execution_model_operations
        operations.reconcile(session, record, payload, actor=user)
        return view(record, request)
