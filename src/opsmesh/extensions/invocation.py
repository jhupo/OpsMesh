"""Single Invocation admission, fixed snapshots and conservative effect receipts."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from jsonschema import ValidationError as SchemaValidationError
from opsmesh_sdk.extensions.hooks import HookDecision, HookInput
from opsmesh_sdk.extensions.operations import OperationResult
from opsmesh_sdk.manifest import HookExport, OperationExport, PackageManifest
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.extensions.access import (
    ScopeAuthorization,
    authorize_installation,
    authorize_instance,
    authorize_invocation,
    installation_viewer,
    load_installation,
)
from opsmesh.extensions.models import ExtensionInstance, Invocation, ManagedDeployment
from opsmesh.extensions.schemas import (
    ExtensionReconcileRequest,
    HookInvocationContext,
    InvokeRequest,
    extension_work,
)
from opsmesh.extensions.security import authorize_permissions
from opsmesh.extensions.service import audit_change, audit_invocation
from opsmesh.extensions.state import get_namespace
from opsmesh.identity.auth.contracts import UserDirectory
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.temporal.dispatch.outbox import stage_execution, stage_signal


def load_invocation(session: Session, invocation_id: UUID, *, lock: bool = False) -> Invocation:
    invocation = session.get(Invocation, invocation_id, with_for_update=lock)
    if invocation is None:
        raise NotFoundError("Extension invocation not found")
    return invocation


def admit_invocation(
    session: Session,
    *,
    user: CurrentUser,
    instance_id: UUID,
    request: InvokeRequest,
    scopes: ScopeAuthorization,
    cipher: SecretCipher,
    parent_id: UUID | None = None,
    source_run_id: UUID | None = None,
    kind: str = "operation",
    dispatch: bool = True,
    hook_context: HookInvocationContext | None = None,
) -> Invocation:
    scopes.require_admission(session)
    instance = authorize_instance(
        session, instance_id, user=user, scopes=scopes, lock=True, execution=True
    )
    installation = load_installation(session, instance.installation_id)
    authorize_installation(session, installation, user=installation_viewer(session, instance))
    if instance.status != "enabled" or installation.status != "enabled":
        raise ForbiddenError("The extension installation or instance is disabled")
    manifest = PackageManifest.model_validate(installation.manifest)
    exports: tuple[OperationExport, ...] | tuple[HookExport, ...] = (
        manifest.operations if kind == "operation" else manifest.hooks
    )
    export = next((item for item in exports if item.export_id == request.export_id), None)
    if export is None:
        raise InvalidArgumentError("The installed package does not declare this export")
    if isinstance(export, HookExport):
        hook_input = HookInput.model_validate(request.arguments)
        if hook_input.stage not in export.stages:
            raise ForbiddenError("The hook does not declare this stage")
        export.input_schema.validate_instance(hook_input.payload)
    else:
        export.input_schema.validate_instance(request.arguments)
    required = authorize_permissions(
        ceiling=installation.permission_ceiling,
        instance=instance.permissions,
        requested=export.permissions,
    )
    if len(canonical_json(request.arguments).encode()) > 1048576:
        raise InvalidArgumentError("Invocation input exceeds the size bound")
    depth = 0
    current = parent_id
    origin_run_id = source_run_id
    while current is not None:
        parent = load_invocation(session, current)
        if parent.principal_id != user.user_id or parent.status != "started":
            raise ForbiddenError("The parent invocation is not active for this principal")
        if parent.instance_id == instance_id and parent.export_id == request.export_id:
            raise ConflictError("An invocation cannot synchronously call itself")
        if parent.snapshot.get("configuration_id") != (
            str(instance.configuration_id) if instance.configuration_id else None
        ):
            raise ForbiddenError("A child invocation cannot change its parent's scope")
        parent_run = parent.snapshot.get("run_id")
        if parent_run:
            parent_run_id = UUID(str(parent_run))
            if origin_run_id is not None and origin_run_id != parent_run_id:
                raise ForbiddenError("A child invocation cannot change its source Run")
            origin_run_id = parent_run_id
        current = parent.parent_id
        depth += 1
        if depth >= 8:
            raise ConflictError("Invocation nesting exceeds depth 8")

    payload = {
        **request.model_dump(mode="json"),
        "parent_id": str(parent_id) if parent_id else None,
        "source_run_id": str(origin_run_id) if origin_run_id else None,
        "kind": kind,
        "hook_context": hook_context.model_dump(mode="json") if hook_context else None,
    }

    def existing(identifier: UUID) -> Invocation:
        invocation = load_invocation(session, identifier)
        if invocation.principal_id != user.user_id:
            raise NotFoundError("Extension invocation not found")
        return invocation

    def create() -> Invocation:
        if hook_context is not None:
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(Invocation)
                    .where(
                        Invocation.snapshot["run_id"].astext == str(hook_context.run_id),
                        Invocation.snapshot["hook_binding"].astext == hook_context.binding_id,
                    )
                )
                or 0
            )
            if count >= hook_context.max_invocations:
                raise ConflictError("The frozen hook invocation budget is exhausted")
        namespace = get_namespace(
            session,
            instance_id=instance.id,
            principal_id=user.user_id,
            scope_key=f"configuration:{instance.configuration_id}"
            if instance.configuration_id
            else f"user:{instance.owner_id}",
        )
        timeout = export.timeout_seconds
        snapshot: dict[str, Any] = {
            "artifact": installation.artifact,
            "namespace": installation.namespace,
            "environment_id": str(instance.environment_id) if instance.environment_id else None,
            "permissions": sorted(required),
            "configuration": instance.configuration,
            "configuration_id": str(instance.configuration_id)
            if instance.configuration_id
            else None,
            "export": export.model_dump(mode="json"),
            "execution_policy": manifest.execution.model_dump(mode="json"),
            "input_digest": payload_digest(request.arguments),
        }
        if origin_run_id is not None:
            snapshot["run_id"] = str(origin_run_id)
        deadline = datetime.now(UTC) + timedelta(
            seconds=min(timeout, manifest.execution.timeout_seconds)
        )
        if hook_context is not None:
            snapshot.update(
                run_id=str(hook_context.run_id),
                hook_binding=hook_context.binding_id,
                hook_budget=hook_context.model_dump(mode="json"),
            )
            deadline = min(
                deadline, datetime.now(UTC) + timedelta(seconds=hook_context.timeout_seconds)
            )
        if parent_id is not None:
            deadline = min(deadline, load_invocation(session, parent_id).deadline)
        invocation = Invocation(
            id=uuid4(),
            instance_id=instance.id,
            installation_id=installation.id,
            principal_id=user.user_id,
            parent_id=parent_id,
            namespace_id=namespace.id,
            export_id=request.export_id,
            operation_id=request.operation_id,
            kind=kind,
            fingerprint=payload_digest(payload),
            instance_revision=instance.revision,
            snapshot=snapshot,
            input_ciphertext=cipher.encrypt_payload(request.arguments).ciphertext,
            deadline=deadline,
            status="queued",
        )
        session.add(invocation)
        session.flush()
        if dispatch:
            stage_execution(session, extension_work(invocation.id, "invoke"))
        audit_invocation(session, invocation)
        return invocation

    return run_idempotent_create(
        session=session,
        scope_id=instance.id,
        actor_id=user.user_id,
        operation="extensions.invoke",
        idempotency_key=request.operation_id,
        request_payload=payload,
        get_existing=existing,
        create=create,
        resource_id=lambda invocation: invocation.id,
    )


def claim_invocation(
    session: Session,
    invocation_id: UUID,
    *,
    users: UserDirectory,
    scopes: ScopeAuthorization,
) -> Invocation:
    scopes.require_admission(session)
    invocation = load_invocation(session, invocation_id)
    policy = PackageManifest.model_validate(
        load_installation(session, invocation.installation_id).manifest
    ).execution
    if policy.mode == "managed_worker":
        # Serialize capacity admission across different Invocation rows.
        session.get(ExtensionInstance, invocation.instance_id, with_for_update=True)
    invocation = load_invocation(session, invocation_id, lock=True)
    if invocation.status != "queued":
        if invocation.status == "started":
            invocation.status = "needs_attention"
            invocation.error_code = "unconfirmed_invocation_effect"
            invocation.revision += 1
            audit_invocation(session, invocation)
        return invocation
    authorize_invocation(session, invocation, users=users, scopes=scopes)
    if policy.mode == "managed_worker":
        deployment = session.scalar(
            select(ManagedDeployment).where(
                ManagedDeployment.instance_id == invocation.instance_id,
                ManagedDeployment.instance_revision == invocation.instance_revision,
                ManagedDeployment.status == "ready",
            )
        )
        if deployment is None:
            raise ConflictError("Explicitly start a ready deployment before invoking this instance")
        used = (
            session.scalar(
                select(func.count())
                .select_from(Invocation)
                .where(
                    Invocation.instance_id == invocation.instance_id,
                    Invocation.status.in_(["started", "needs_attention"]),
                )
            )
            or 0
        )
        if used >= policy.max_concurrency:
            return invocation
        invocation.snapshot = {**invocation.snapshot, "deployment_id": str(deployment.id)}
    invocation.status = "started"
    invocation.revision += 1
    audit_invocation(session, invocation)
    return invocation


def complete_invocation(
    session: Session,
    invocation_id: UUID,
    *,
    result: dict[str, Any] | None,
    cipher: SecretCipher,
    error_code: str | None = None,
    confirmed_not_started: bool = False,
) -> Invocation:
    invocation = load_invocation(session, invocation_id, lock=True)
    if invocation.status in {"completed", "failed", "cancelled"}:
        return invocation
    if result is None or result.get("needs_attention"):
        invocation.status = (
            "cancelled"
            if confirmed_not_started and invocation.cancel_requested
            else "failed"
            if confirmed_not_started
            else "needs_attention"
        )
        invocation.error_code = error_code or "unconfirmed_invocation_effect"
    else:
        try:
            if invocation.kind == "hook":
                TypeAdapter(HookDecision).validate_python(result)
            else:
                output = OperationResult.model_validate(result)
                declaration = OperationExport.model_validate(invocation.snapshot["export"])
                declaration.output_schema.validate_instance(output.value)
                if not set(output.effects) <= set(declaration.effects):
                    raise ValueError("The result reports undeclared effects")
            if len(canonical_json(result).encode()) > 1048576:
                raise ValueError("Invocation result exceeds its byte bound")
        except (ValueError, SchemaValidationError, ForbiddenError):
            invocation.status = "needs_attention"
            invocation.error_code = "invalid_isolated_result"
        else:
            invocation.status = "cancelled" if invocation.cancel_requested else "completed"
            invocation.result_ciphertext = cipher.encrypt_payload(result).ciphertext
            invocation.effects = list(result.get("effects", []))
    invocation.revision += 1
    audit_invocation(session, invocation)
    return invocation


def cancel_invocation(
    session: Session,
    invocation: Invocation,
    *,
    user: CurrentUser,
    expected_revision: int,
    authorize_owner: Callable[[Session, Invocation, CurrentUser], None] | None = None,
) -> None:
    if authorize_owner is not None:
        authorize_owner(session, invocation, user)
    elif invocation.principal_id != user.user_id:
        raise NotFoundError("Extension invocation not found")
    if invocation.revision != expected_revision:
        raise ConflictError("Invocation revision changed")
    if invocation.status in {"completed", "failed", "cancelled", "needs_attention"}:
        raise ConflictError("The invocation is no longer cancellable")
    invocation.cancel_requested = True
    invocation.revision += 1
    if invocation.status == "queued":
        invocation.status = "cancelled"
        audit_change(session, user, "invocation.cancelled", invocation.id)
        return
    stage_signal(
        session,
        dispatch_id=uuid4(),
        workflow_id=extension_work(invocation.id, "invoke").workflow_id,
        signal_name="cancel",
        signal_args=(),
    )
    audit_change(session, user, "invocation.cancel_requested", invocation.id)


def reconcile_invocation(
    session: Session,
    invocation: Invocation,
    *,
    user: CurrentUser,
    request: ExtensionReconcileRequest,
    cipher: SecretCipher,
) -> None:
    if invocation.principal_id != user.user_id:
        raise NotFoundError("Extension invocation not found")
    if invocation.status != "needs_attention" or invocation.revision != request.expected_revision:
        raise ConflictError("The invocation is not awaiting this reconciliation")
    # A host model/API effect has its own owner receipt. Resolve that receipt
    # before the enclosing Invocation; evidence never restarts the operation.
    from opsmesh.extensions.models import HostCallReceipt

    unresolved = session.scalar(
        select(HostCallReceipt.id)
        .where(
            HostCallReceipt.invocation_id == invocation.id,
            HostCallReceipt.status.in_(["started", "needs_attention"]),
        )
        .limit(1)
    )
    if unresolved:
        raise ConflictError("Reconcile unresolved host effects with their owner first")
    invocation.reconciliation = request.model_dump(mode="json")
    invocation.status = "completed" if request.outcome == "completed" else "failed"
    invocation.result_ciphertext = cipher.encrypt_payload(request.result).ciphertext
    invocation.revision += 1
    audit_change(session, user, "invocation.reconciled", invocation.id)
