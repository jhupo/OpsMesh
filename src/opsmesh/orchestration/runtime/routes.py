"""Explicit environment lifecycle and asynchronous sandbox operation APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import CurrentUserDep
from opsmesh.orchestration.contracts import ExecutionBindings, ExecutionScope, ExecutionSubject
from opsmesh.orchestration.dependencies import execution_cipher as cipher_of
from opsmesh.orchestration.dependencies import scope_admission as _scope_admission
from opsmesh.orchestration.runtime import control, service
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.models import EnvironmentOperationReceipt
from opsmesh.orchestration.runtime.schemas import (
    EnvironmentCommandRequest,
    EnvironmentCreateRequest,
    EnvironmentFileDownloadRequest,
    EnvironmentFileUploadRequest,
    EnvironmentOperationRequest,
    EnvironmentView,
    RuntimeOperationView,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http.dependencies import get_db_session

router = APIRouter(prefix="/orchestration/environments", tags=["orchestration"])
DbSession = Annotated[Session, Depends(get_db_session)]
Key = Annotated[str, Query(min_length=1, max_length=200)]


@router.post(
    "",
    response_model=EnvironmentView,
    status_code=202,
    operation_id="orchestration.environments.create",
)
def create_environment(
    payload: EnvironmentCreateRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> EnvironmentView:
    with session_transaction(session):
        image_provider = getattr(request.app.state, "installed_sandbox_images", None)
        installed = image_provider(session, user) if image_provider else frozenset()
        record = service.create_environment(
            session,
            subject=ExecutionSubject(user.user_id),
            scope=ExecutionScope(
                configuration_id=payload.configuration_id,
            ),
            admission=_scope_admission(request),
            image=payload.image,
            root_directory_id=payload.root_directory_id,
            allowed_images=frozenset(getattr(request.app.state, "execution_sandbox_images", []))
            | installed,
            operation_id=idempotency_key,
        )
    return EnvironmentView(
        environment_id=record.id,
        scope_id=record.scope_id,
        owner_ref=record.owner_ref,
        status=record.status,
        error_code=record.error_code,
    )


@router.get(
    "/{environment_id}",
    response_model=EnvironmentView,
    operation_id="orchestration.environments.get",
)
def get_environment(
    environment_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> EnvironmentView:
    record = load_environment(
        session,
        environment_id=environment_id,
        subject=ExecutionSubject(user.user_id),
        admission=_scope_admission(request),
    )
    return EnvironmentView(
        environment_id=record.id,
        scope_id=record.scope_id,
        owner_ref=record.owner_ref,
        status=record.status,
        error_code=record.error_code,
    )


@router.post(
    "/{environment_id}/resume",
    response_model=RuntimeOperationView,
    status_code=202,
    operation_id="orchestration.environments.resume",
)
def resume_environment(
    environment_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    return submit_operation(
        environment_id,
        EnvironmentOperationRequest(action="resume"),
        user,
        session,
        request,
        idempotency_key,
    )


@router.post(
    "/{environment_id}/close",
    response_model=RuntimeOperationView,
    status_code=202,
    operation_id="orchestration.environments.close",
)
def close_environment(
    environment_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    return submit_operation(
        environment_id,
        EnvironmentOperationRequest(action="close"),
        user,
        session,
        request,
        idempotency_key,
    )


@router.post(
    "/{environment_id}/files/upload",
    response_model=RuntimeOperationView,
    status_code=202,
    operation_id="orchestration.environments.files.upload",
)
def upload_file(
    environment_id: UUID,
    payload: EnvironmentFileUploadRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    return submit_operation(
        environment_id,
        EnvironmentOperationRequest(action="write", **payload.model_dump()),
        user,
        session,
        request,
        idempotency_key,
    )


@router.post(
    "/{environment_id}/files/download",
    response_model=RuntimeOperationView,
    status_code=202,
    operation_id="orchestration.environments.files.download",
)
def download_file(
    environment_id: UUID,
    payload: EnvironmentFileDownloadRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    return submit_operation(
        environment_id,
        EnvironmentOperationRequest(action="read", path=payload.path),
        user,
        session,
        request,
        idempotency_key,
    )


@router.post(
    "/{environment_id}/commands",
    response_model=RuntimeOperationView,
    status_code=202,
    operation_id="orchestration.environments.commands.execute",
)
def execute_command(
    environment_id: UUID,
    payload: EnvironmentCommandRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    return submit_operation(
        environment_id,
        EnvironmentOperationRequest(action="command", **payload.model_dump()),
        user,
        session,
        request,
        idempotency_key,
    )


def submit_operation(
    environment_id: UUID,
    payload: EnvironmentOperationRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    with session_transaction(session):
        receipt = service.submit_operation(
            session,
            subject=ExecutionSubject(user.user_id),
            admission=_scope_admission(request),
            environment_id=environment_id,
            operation_id=idempotency_key,
            request=payload,
        )
    return RuntimeOperationView(
        operation_id=receipt.operation_id, status=receipt.status, revision=receipt.revision
    )


@router.get(
    "/{environment_id}/operations/{operation_id}",
    response_model=RuntimeOperationView,
    operation_id="orchestration.environments.operations.get",
)
def get_operation(
    environment_id: UUID,
    operation_id: str,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> RuntimeOperationView:
    record = load_environment(
        session,
        environment_id=environment_id,
        subject=ExecutionSubject(user.user_id),
        admission=_scope_admission(request),
    )
    receipt = session.get(EnvironmentOperationReceipt, (record.scope_id, operation_id))
    if receipt is None or receipt.environment_id != record.id:
        raise NotFoundError("The environment operation does not exist")
    result = (
        cipher_of(request).decrypt_payload(receipt.encrypted_result)
        if receipt.encrypted_result
        else None
    )
    return RuntimeOperationView(
        operation_id=receipt.operation_id,
        status=receipt.status,
        result=result,
        revision=receipt.revision,
    )


@router.post(
    "/{environment_id}/operations/{operation_id}/cancel",
    response_model=RuntimeOperationView,
    operation_id="orchestration.environments.operations.cancel",
)
def cancel_operation(
    environment_id: UUID,
    operation_id: str,
    payload: control.RuntimeControlRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> RuntimeOperationView:
    with session_transaction(session):
        owner = load_environment(
            session,
            environment_id=environment_id,
            subject=ExecutionSubject(user.user_id),
            admission=_scope_admission(request),
            lock=True,
        )
        receipt = session.get(EnvironmentOperationReceipt, (owner.scope_id, operation_id))
        if receipt is None or receipt.environment_id != owner.id:
            raise NotFoundError("The environment operation does not exist")
        control.cancel(session, receipt=receipt, actor_id=user.user_id, request=payload)
    return RuntimeOperationView(
        operation_id=receipt.operation_id, status=receipt.status, revision=receipt.revision
    )


@router.post(
    "/{environment_id}/operations/{operation_id}/reconcile",
    response_model=RuntimeOperationView,
    operation_id="orchestration.environments.operations.reconcile",
)
def reconcile_operation(
    environment_id: UUID,
    operation_id: str,
    payload: control.RuntimeReconcileRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> RuntimeOperationView:
    bindings = request.app.state.execution_bindings
    assert isinstance(bindings, ExecutionBindings)
    with session_transaction(session):
        receipt = control.reconcile(
            session,
            kind="environment",
            owner_id=environment_id,
            operation_id=operation_id,
            actor=user,
            request=payload,
            cipher=cipher_of(request),
            bindings=bindings,
            storage=request.app.state.execution_storage,
        )
    return RuntimeOperationView(
        operation_id=receipt.operation_id, status=receipt.status, revision=receipt.revision
    )
