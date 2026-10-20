"""Cancel one operation or attest existing runtime effects without repeating I/O."""

from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.contracts import ExecutionBindings
from opsmesh.orchestration.runtime.activities import reclaim_files, settle_file
from opsmesh.orchestration.runtime.contracts import RuntimeStorage
from opsmesh.orchestration.runtime.models import (
    Environment,
    EnvironmentOperationReceipt,
    MCPConnection,
    MCPOperationReceipt,
)
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.dispatch.outbox import stage_signal

RuntimeReceipt = EnvironmentOperationReceipt | MCPOperationReceipt


class RuntimeControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: UUID
    expected_revision: int = Field(ge=0)


class RuntimeReconcileRequest(RuntimeControlRequest):
    unresolved_operations: list[str] = Field(min_length=1)
    evidence: dict[str, Any] = Field(min_length=1)
    confirmed_status: Literal["ready", "closed"]
    result: dict[str, Any] = Field(default_factory=dict)


def cancel(
    session: Session, *, receipt: RuntimeReceipt, actor_id: UUID, request: RuntimeControlRequest
) -> RuntimeReceipt:
    def signal() -> RuntimeReceipt:
        if receipt.revision != request.expected_revision:
            raise ConflictError("The runtime receipt revision changed")
        if receipt.status == "completed":
            return receipt
        if receipt.workflow_id is None and receipt.status != "pending":
            raise ConflictError("The runtime receipt has no durable execution reference")
        if receipt.workflow_id is not None:
            stage_signal(
                session,
                dispatch_id=uuid4(),
                workflow_id=receipt.workflow_id,
                signal_name="cancel",
                signal_args=(),
            )
        receipt.revision += 1
        # A queued operation has not performed I/O. Completing its receipt also
        # prevents a delayed Activity from claiming it after cancellation.
        if receipt.status == "pending":
            receipt.status = "completed"
            if isinstance(receipt, EnvironmentOperationReceipt):
                environment = session.get(Environment, receipt.environment_id)
                if environment is not None:
                    environment.active_operation_id = None
                    environment.status = "closed" if receipt.action == "create" else "ready"
            else:
                connection = session.get(MCPConnection, receipt.connection_id)
                if connection is not None:
                    connection.status = "closed" if receipt.action == "connect" else "ready"
        return receipt

    return run_idempotent_create(
        session=session,
        scope_id=receipt.scope_id,
        actor_id=actor_id,
        operation=f"execution.runtime.cancel:{receipt.operation_id}",
        idempotency_key=str(request.command_id),
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda _: receipt,
        create=signal,
        resource_id=lambda _: request.command_id,
    )


def reconcile(
    session: Session,
    *,
    kind: Literal["environment", "mcp"],
    owner_id: UUID,
    operation_id: str,
    actor: CurrentUser,
    request: RuntimeReconcileRequest,
    cipher: SecretCipher,
    bindings: ExecutionBindings,
    storage: RuntimeStorage,
) -> RuntimeReceipt:
    require_platform_admin(actor)
    if kind == "environment":
        owner: Environment | MCPConnection | None = session.scalar(
            select(Environment).where(Environment.id == owner_id).with_for_update()
        )
        rows = list(
            session.scalars(
                select(EnvironmentOperationReceipt).where(
                    EnvironmentOperationReceipt.environment_id == owner_id
                )
            )
        )
    else:
        owner = session.scalar(
            select(MCPConnection).where(MCPConnection.id == owner_id).with_for_update()
        )
        rows = list(
            session.scalars(
                select(MCPOperationReceipt).where(MCPOperationReceipt.connection_id == owner_id)
            )
        )
    if owner is None:
        raise NotFoundError("The runtime owner does not exist")
    receipt = next((row for row in rows if row.operation_id == operation_id), None)
    if receipt is None:
        raise NotFoundError("The runtime operation does not exist")

    def confirm() -> RuntimeReceipt:
        unresolved = {row.operation_id for row in rows if row.status in {"started", "uncertain"}}
        if (
            set(request.unresolved_operations) != unresolved
            or len(request.unresolved_operations) != len(unresolved)
            or unresolved != {operation_id}
            or receipt.revision != request.expected_revision
        ):
            raise ConflictError(
                "Confirm the complete unresolved runtime set at its current revision"
            )
        receipt.status = "completed"
        receipt.revision += 1
        if isinstance(owner, Environment):
            if owner.active_operation_id != operation_id:
                raise ConflictError("Another operation owns the environment")
            if request.confirmed_status == "ready" and owner.encrypted_state is None:
                raise ConflictError("No native state exists to resume this environment")
            assert isinstance(receipt, EnvironmentOperationReceipt)
            if request.confirmed_status == "closed":
                if receipt.storage_reservation_id is not None:
                    storage.release_storage(session, reservation_id=receipt.storage_reservation_id)
                reclaim_files(session, owner.id, storage)
            elif receipt.action == "write":
                settle_file(session, owner, receipt, request.result, storage)
            if request.confirmed_status == "closed" and owner.capacity_reservation_id is not None:
                bindings.capacity.release_capacity(
                    session,
                    user_id=owner.principal_id,
                    reservation_id=owner.capacity_reservation_id,
                )
                owner.capacity_reservation_id = None
            owner.active_operation_id = None
            assert isinstance(receipt, EnvironmentOperationReceipt)
            encrypted = cipher.encrypt_payload(
                {
                    **request.result,
                    "reconciliation_evidence": request.evidence,
                    "confirmed_by": str(actor.user_id),
                }
            )
            receipt.encrypted_result, receipt.encryption_key_id = (
                encrypted.ciphertext,
                encrypted.key_id,
            )
        else:
            assert isinstance(receipt, MCPOperationReceipt)
            receipt.result = {
                **request.result,
                "reconciliation_evidence": request.evidence,
                "confirmed_by": str(actor.user_id),
            }
        owner.status = request.confirmed_status
        return receipt

    return run_idempotent_create(
        session=session,
        scope_id=owner.scope_id,
        actor_id=actor.user_id,
        operation=f"execution.runtime.reconcile:{kind}:{owner_id}:{operation_id}",
        idempotency_key=str(request.command_id),
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda _: receipt,
        create=confirm,
        resource_id=lambda _: request.command_id,
    )
