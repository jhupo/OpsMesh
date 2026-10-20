"""Extension administration, owner configuration, asynchronous calls and state export."""

from pathlib import Path
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.extensions import deployment, invocation, service, state, state_transfer
from opsmesh.extensions.access import (
    ScopeAuthorization,
    authorize_installation,
    authorize_instance,
    load_installation,
    require_admin,
)
from opsmesh.extensions.contracts import InstanceReferences
from opsmesh.extensions.models import ExtensionNamespace, HostCallReceipt, Installation
from opsmesh.extensions.schemas import (
    CleanupPreview,
    CleanupRequest,
    CleanupResult,
    DeploymentRequest,
    DeploymentView,
    ExtensionReconcileRequest,
    HostCallView,
    InstallationView,
    InstallRequest,
    InstanceConfiguration,
    InstanceUpdate,
    InstanceView,
    InvocationView,
    InvokeRequest,
    RevisionRequest,
    StatePage,
    StateRestoreRequest,
    StateRestoreResult,
    StateSnapshot,
)
from opsmesh.identity.auth.dependencies import CurrentUserDep
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.http.dependencies import get_db_session

router = APIRouter(prefix="/extensions", tags=["extensions"])
DbSession = Annotated[Session, Depends(get_db_session)]


def scopes(request: Request) -> ScopeAuthorization:
    return cast(ScopeAuthorization, request.app.state.extension_scopes)


def invocation_view(record: Any, request: Request) -> InvocationView:
    result = (
        request.app.state.secret_cipher.decrypt_payload(record.result_ciphertext)
        if record.result_ciphertext
        else None
    )
    return InvocationView.model_validate(
        {
            **{key: getattr(record, key) for key in InvocationView.model_fields if key != "result"},
            "result": result,
        }
    )


@router.post(
    "/installations",
    status_code=202,
    response_model=InstallationView,
    operation_id="extensions.installations.create",
)
def install(
    payload: InstallRequest, user: CurrentUserDep, session: DbSession, request: Request
) -> InstallationView:
    with session_transaction(session):
        record = service.request_installation(
            session,
            user=user,
            request=payload,
            storage_root=Path(request.app.state.settings.storage_root),
        )
        return InstallationView.model_validate(record)


@router.get(
    "/installations",
    response_model=list[InstallationView],
    operation_id="extensions.installations.list",
)
def installations(
    user: CurrentUserDep, session: DbSession, limit: int = 50, offset: int = 0
) -> list[InstallationView]:
    require_admin(user)
    if not 1 <= limit <= 200 or offset < 0:
        raise ConflictError("Invalid installation page")
    return [
        InstallationView.model_validate(row)
        for row in session.scalars(
            select(Installation).order_by(Installation.id).limit(limit).offset(offset)
        )
    ]


@router.get(
    "/installations/{installation_id}",
    response_model=InstallationView,
    operation_id="extensions.installations.get",
)
def installation(
    installation_id: UUID, user: CurrentUserDep, session: DbSession
) -> InstallationView:
    # Enabled definitions are discoverable; only the administrator sees failures.
    record = load_installation(session, installation_id)
    authorize_installation(session, record, user=user)
    if record.status != "enabled":
        require_admin(user)
    return InstallationView.model_validate(record)


@router.post(
    "/installations/{installation_id}/disable",
    response_model=InstallationView,
    operation_id="extensions.installations.disable",
)
def disable(
    installation_id: UUID, payload: RevisionRequest, user: CurrentUserDep, session: DbSession
) -> InstallationView:
    with session_transaction(session):
        return InstallationView.model_validate(
            service.change_installation(
                session,
                user=user,
                installation_id=installation_id,
                expected_revision=payload.expected_revision,
                status="disabled",
            )
        )


@router.post(
    "/installations/{installation_id}/enable",
    response_model=InstallationView,
    operation_id="extensions.installations.enable",
)
def enable(
    installation_id: UUID, payload: RevisionRequest, user: CurrentUserDep, session: DbSession
) -> InstallationView:
    with session_transaction(session):
        return InstallationView.model_validate(
            service.change_installation(
                session,
                user=user,
                installation_id=installation_id,
                expected_revision=payload.expected_revision,
                status="enabled",
            )
        )


@router.post(
    "/installations/{installation_id}/uninstall",
    status_code=202,
    response_model=InstallationView,
    operation_id="extensions.installations.uninstall",
)
def uninstall(
    installation_id: UUID, payload: RevisionRequest, user: CurrentUserDep, session: DbSession
) -> InstallationView:
    with session_transaction(session):
        return InstallationView.model_validate(
            service.change_installation(
                session,
                user=user,
                installation_id=installation_id,
                expected_revision=payload.expected_revision,
                status="draining",
            )
        )


@router.post(
    "/instances",
    status_code=201,
    response_model=InstanceView,
    operation_id="extensions.instances.create",
)
def create_instance(
    payload: InstanceConfiguration, user: CurrentUserDep, session: DbSession, request: Request
) -> InstanceView:
    with session_transaction(session):
        return InstanceView.model_validate(
            service.configure_instance(
                session,
                user=user,
                request=payload,
                scopes=scopes(request),
                references=cast(InstanceReferences, request.app.state.extension_references),
            )
        )


@router.get(
    "/instances/{instance_id}", response_model=InstanceView, operation_id="extensions.instances.get"
)
def get_instance(
    instance_id: UUID, user: CurrentUserDep, session: DbSession, request: Request
) -> InstanceView:
    return InstanceView.model_validate(
        authorize_instance(session, instance_id, user=user, scopes=scopes(request))
    )


@router.put(
    "/instances/{instance_id}",
    response_model=InstanceView,
    operation_id="extensions.instances.update",
)
def update_instance(
    instance_id: UUID,
    payload: InstanceUpdate,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> InstanceView:
    with session_transaction(session):
        return InstanceView.model_validate(
            service.configure_instance(
                session,
                user=user,
                request=payload,
                scopes=scopes(request),
                instance_id=instance_id,
                expected_revision=payload.expected_revision,
                references=cast(InstanceReferences, request.app.state.extension_references),
            )
        )


@router.post(
    "/instances/{instance_id}/deployments",
    status_code=202,
    response_model=DeploymentView,
    operation_id="extensions.deployments.create",
)
def start_deployment(
    instance_id: UUID,
    payload: DeploymentRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> DeploymentView:
    with session_transaction(session):
        return DeploymentView.model_validate(
            deployment.request_start(
                session,
                user=user,
                instance_id=instance_id,
                request=payload,
                scopes=scopes(request),
            )
        )


@router.get(
    "/deployments/{deployment_id}",
    response_model=DeploymentView,
    operation_id="extensions.deployments.get",
)
def get_deployment(
    deployment_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> DeploymentView:
    record = deployment.load_deployment(session, deployment_id)
    authorize_instance(session, record.instance_id, user=user, scopes=scopes(request))
    return DeploymentView.model_validate(record)


@router.post(
    "/deployments/{deployment_id}/stop",
    status_code=202,
    response_model=DeploymentView,
    operation_id="extensions.deployments.stop",
)
def stop_deployment(
    deployment_id: UUID,
    payload: RevisionRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> DeploymentView:
    with session_transaction(session):
        return DeploymentView.model_validate(
            deployment.request_stop(
                session,
                user=user,
                deployment_id=deployment_id,
                expected_revision=payload.expected_revision,
                scopes=scopes(request),
            )
        )


@router.post(
    "/instances/{instance_id}/invocations",
    status_code=202,
    response_model=InvocationView,
    operation_id="extensions.invocations.create",
)
def invoke(
    instance_id: UUID,
    payload: InvokeRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> InvocationView:
    with session_transaction(session):
        return invocation_view(
            invocation.admit_invocation(
                session,
                user=user,
                instance_id=instance_id,
                request=payload,
                scopes=scopes(request),
                cipher=request.app.state.secret_cipher,
            ),
            request,
        )


def owned_invocation(
    session: Session, identifier: UUID, user: Any, request: Request, *, lock: bool = False
) -> Any:
    record = invocation.load_invocation(session, identifier, lock=lock)
    if record.principal_id != user.user_id:
        raise NotFoundError("Extension invocation not found")
    authorize_instance(
        session, record.instance_id, user=user, scopes=scopes(request), execution=True
    )
    return record


@router.get(
    "/invocations/{invocation_id}",
    response_model=InvocationView,
    operation_id="extensions.invocations.get",
)
def get_invocation(
    invocation_id: UUID, user: CurrentUserDep, session: DbSession, request: Request
) -> InvocationView:
    return invocation_view(owned_invocation(session, invocation_id, user, request), request)


@router.post(
    "/invocations/{invocation_id}/cancel",
    response_model=InvocationView,
    operation_id="extensions.invocations.cancel",
)
def cancel(
    invocation_id: UUID,
    payload: RevisionRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> InvocationView:
    with session_transaction(session):
        record = owned_invocation(session, invocation_id, user, request, lock=True)
        invocation.cancel_invocation(
            session, record, user=user, expected_revision=payload.expected_revision
        )
        return invocation_view(record, request)


@router.post(
    "/invocations/{invocation_id}/reconcile",
    response_model=InvocationView,
    operation_id="extensions.invocations.reconcile",
)
def reconcile(
    invocation_id: UUID,
    payload: ExtensionReconcileRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> InvocationView:
    with session_transaction(session):
        record = owned_invocation(session, invocation_id, user, request, lock=True)
        invocation.reconcile_invocation(
            session, record, user=user, request=payload, cipher=request.app.state.secret_cipher
        )
        return invocation_view(record, request)


def owned_namespace(
    session: Session, identifier: UUID, user: Any, request: Request
) -> ExtensionNamespace:
    record = session.get(ExtensionNamespace, identifier)
    if record is None or record.principal_id != user.user_id:
        raise NotFoundError("Extension namespace not found")
    instance = authorize_instance(
        session, record.instance_id, user=user, scopes=scopes(request), execution=True
    )
    expected = (
        f"configuration:{instance.configuration_id}"
        if instance.configuration_id
        else (f"user:{instance.owner_id}")
    )
    if record.scope_key != expected:
        raise ForbiddenError("The extension namespace is outside the instance scope")
    scopes(request).admit(
        session,
        user_id=user.user_id,
        configuration_id=instance.configuration_id,
    )
    return record


@router.get(
    "/invocations/{invocation_id}/host-calls",
    response_model=list[HostCallView],
    operation_id="extensions.invocations.host_calls.list",
)
def host_calls(
    invocation_id: UUID, user: CurrentUserDep, session: DbSession, request: Request
) -> list[HostCallView]:
    owned_invocation(session, invocation_id, user, request)
    return [
        HostCallView.model_validate(row)
        for row in session.scalars(
            select(HostCallReceipt)
            .where(HostCallReceipt.invocation_id == invocation_id)
            .order_by(HostCallReceipt.id)
            .limit(1024)
        )
    ]


@router.post(
    "/invocations/{invocation_id}/host-calls/{receipt_id}/reconcile",
    response_model=HostCallView,
    operation_id="extensions.invocations.host_calls.reconcile",
)
def reconcile_host_call(
    invocation_id: UUID,
    receipt_id: UUID,
    payload: ExtensionReconcileRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> HostCallView:
    with session_transaction(session):
        record = owned_invocation(session, invocation_id, user, request, lock=True)
        if record.status != "needs_attention":
            raise ConflictError("The Invocation must be awaiting effect reconciliation")
        receipt = session.get(HostCallReceipt, receipt_id, with_for_update=True)
        if receipt is None or receipt.invocation_id != invocation_id:
            raise NotFoundError("The host call is unavailable")
        if receipt.status not in {"started", "needs_attention"}:
            raise ConflictError("The host call is already resolved")
        if receipt.revision != payload.expected_revision:
            raise ConflictError("The host call revision changed")
        request.app.state.extension_reconciliation.verify(session, record, receipt, payload)
        receipt.result_ciphertext = request.app.state.secret_cipher.encrypt_payload(
            payload.result
        ).ciphertext
        receipt.reconciliation = payload.model_dump(mode="json")
        receipt.status = "completed"
        receipt.revision += 1
        service.audit_change(session, user, "host_call.reconciled", receipt.id)
        return HostCallView.model_validate(receipt)


@router.get(
    "/namespaces/{namespace_id}/state",
    response_model=StatePage,
    operation_id="extensions.state.export",
)
def export_state(
    namespace_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    cursor: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    owned_namespace(session, namespace_id, user, request)
    return state.list_values(session, namespace_id, cursor=cursor, limit=limit)


@router.get(
    "/namespaces/{namespace_id}/snapshot",
    response_model=StateSnapshot,
    operation_id="extensions.state.snapshot",
)
def snapshot_state(
    namespace_id: UUID, user: CurrentUserDep, session: DbSession, request: Request
) -> StateSnapshot:
    with session_transaction(session):
        owned_namespace(session, namespace_id, user, request)
        return StateSnapshot.model_validate(state_transfer.export_state(session, namespace_id))


@router.post(
    "/namespaces/{namespace_id}/restore",
    response_model=StateRestoreResult,
    operation_id="extensions.state.restore",
)
def restore_state(
    namespace_id: UUID,
    payload: StateRestoreRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> StateRestoreResult:
    with session_transaction(session):
        owned_namespace(session, namespace_id, user, request)
        restored = state_transfer.restore_state(
            session,
            namespace_id,
            payload.snapshot.model_dump(mode="json"),
            payload.expected_revision,
        )
        service.audit_change(session, user, "state.restored", namespace_id)
        return StateRestoreResult(restored_entries=restored)


@router.get(
    "/namespaces/{namespace_id}/cleanup-preview",
    response_model=CleanupPreview,
    operation_id="extensions.state.cleanup_preview",
)
def preview(
    namespace_id: UUID, user: CurrentUserDep, session: DbSession, request: Request
) -> dict[str, Any]:
    with session_transaction(session):
        owned_namespace(session, namespace_id, user, request)
        return state.cleanup_preview(session, namespace_id)


@router.post(
    "/namespaces/{namespace_id}/cleanup",
    response_model=CleanupResult,
    operation_id="extensions.state.cleanup",
)
def cleanup(
    namespace_id: UUID,
    payload: CleanupRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> dict[str, bool]:
    with session_transaction(session):
        namespace = owned_namespace(session, namespace_id, user, request)
        instance = authorize_instance(
            session, namespace.instance_id, user=user, scopes=scopes(request), lock=True
        )
        active = [
            call
            for call in service.active_invocations(session, instance.installation_id)
            if call.namespace_id == namespace_id
        ]
        if active:
            raise ConflictError("Namespace cleanup requires all invocations to be resolved")
        state.cleanup_namespace(
            session,
            namespace_id,
            expected_revision=payload.expected_revision,
            preview_digest=payload.preview_digest,
        )
        service.audit_change(session, user, "state.cleaned", namespace_id)
        return {"cleaned": True}
