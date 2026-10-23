"""Environment ownership and explicit persistent sandbox operations."""

from collections.abc import Callable
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.execution.runtime import service
from opsmesh.execution.runtime.access import get_environment
from opsmesh.execution.runtime.models import SandboxEnvironment
from opsmesh.execution.runtime.schemas import (
    EnvironmentCreate,
    EnvironmentReconciliation,
    EnvironmentView,
    OperationCreate,
    OperationView,
    RuntimeCommand,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession

router = APIRouter(prefix="/execution/environments", tags=["execution-runtime"])


def secret_cipher(request: Request) -> SecretCipher:
    cipher = getattr(request.app.state, "secret_cipher", None)
    if not isinstance(cipher, SecretCipher):
        raise HTTPException(503, "Execution encryption is unavailable")
    return cipher


def operation_dispatcher(request: Request) -> Callable[[Session, RuntimeCommand], None]:
    dispatcher = getattr(request.app.state, "execution_operation_dispatcher", None)
    if dispatcher is None:
        raise HTTPException(503, "Sandbox operation dispatch is not configured")
    return cast("Callable[[Session, RuntimeCommand], None]", dispatcher)


@router.post("", status_code=202, operation_id="execution.environments.create")
def create(
    payload: EnvironmentCreate, session: DbSession, user: CurrentUserDep, request: Request
) -> EnvironmentView:
    dispatch = operation_dispatcher(request)
    with session_transaction(session):
        row = service.create_environment(
            session,
            actor=user,
            request=payload,
            cipher=secret_cipher(request),
            allowed_images=getattr(request.app.state, "execution_sandbox_images", ()),
        )
        assert row.active_operation_id is not None
        dispatch(
            session, RuntimeCommand(execution_id=row.active_operation_id, environment_id=row.id)
        )
        return EnvironmentView.model_validate(row)


@router.get("/{environment_id}", operation_id="execution.environments.read")
def read(environment_id: UUID, session: DbSession, user: CurrentUserDep) -> EnvironmentView:
    return EnvironmentView.model_validate(
        get_environment(session, actor=user, environment_id=environment_id)
    )


@router.get("", operation_id="execution.environments.list")
def list_environments(
    session: DbSession,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[EnvironmentView]:
    return [
        EnvironmentView.model_validate(row)
        for row in session.scalars(
            select(SandboxEnvironment)
            .where(SandboxEnvironment.owner_user_id == user.user_id)
            .order_by(SandboxEnvironment.created_at.desc(), SandboxEnvironment.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.post(
    "/{environment_id}/operations",
    status_code=202,
    operation_id="execution.environments.operations.create",
)
def operate(
    environment_id: UUID,
    payload: OperationCreate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> OperationView:
    dispatch = operation_dispatcher(request)
    with session_transaction(session):
        row = service.request_operation(
            session,
            actor=user,
            environment_id=environment_id,
            request=payload,
            cipher=secret_cipher(request),
        )
        dispatch(session, RuntimeCommand(execution_id=row.id, environment_id=environment_id))
        return OperationView.model_validate(row)


@router.post(
    "/{environment_id}/reconciliation",
    status_code=202,
    operation_id="execution.environments.reconcile",
)
def reconcile(
    environment_id: UUID,
    payload: EnvironmentReconciliation,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> OperationView:
    dispatch = operation_dispatcher(request)
    with session_transaction(session):
        row = service.reconcile_environment(
            session,
            actor=user,
            environment_id=environment_id,
            request=payload,
            cipher=secret_cipher(request),
        )
        dispatch(session, RuntimeCommand(execution_id=row.id, environment_id=environment_id))
        return OperationView.model_validate(row)


@router.get(
    "/{environment_id}/operations/{operation_id}",
    operation_id="execution.environments.operations.read",
)
def read_operation(
    environment_id: UUID, operation_id: UUID, session: DbSession, user: CurrentUserDep
) -> OperationView:
    return OperationView.model_validate(
        service.get_operation(
            session,
            actor=user,
            environment_id=environment_id,
            operation_id=operation_id,
        )
    )


@router.get(
    "/{environment_id}/operations/{operation_id}/result",
    operation_id="execution.environments.operations.result",
)
def read_result(
    environment_id: UUID,
    operation_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    row = service.get_operation(
        session, actor=user, environment_id=environment_id, operation_id=operation_id
    )
    if row.status != "completed" or row.encrypted_result is None:
        raise HTTPException(409, "The operation has no confirmed result")
    return secret_cipher(request).decrypt_payload(row.encrypted_result, key_id=row.result_key_id)
