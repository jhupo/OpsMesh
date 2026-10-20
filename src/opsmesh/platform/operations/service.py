"""Register commands and Outbox atomically; cancellation checkpoints between batches."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.context import CurrentUser
from opsmesh.platform.operations.handlers import OperationHandlers, resolve
from opsmesh.platform.operations.models import PlatformOperation
from opsmesh.platform.operations.workflow import PlatformOperationExecution
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.serialization import canonical_json
from opsmesh.temporal.dispatch.outbox import stage_execution

TERMINAL = {"completed", "cancelled", "needs_attention"}


def submit(
    session: Session,
    *,
    owner: str,
    command: str,
    target: dict[str, object],
    idempotency_key: str,
    actor: CurrentUser,
    handlers: OperationHandlers,
) -> PlatformOperation:
    require_platform_admin(actor)
    resolve(handlers, owner=owner, command=command)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"platform.operation-owner:{owner}"},
    )
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"platform.operation:{idempotency_key}"},
    )
    existing = session.scalar(
        select(PlatformOperation).where(PlatformOperation.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if (existing.owner, existing.command, existing.target, existing.requested_by_user_id) != (
            owner,
            command,
            target,
            actor.user_id,
        ):
            raise ConflictError("The operation idempotency key was reused with different input")
        return existing
    operation = PlatformOperation(
        owner=owner,
        command=command,
        target=target,
        idempotency_key=idempotency_key,
        requested_by_user_id=actor.user_id,
    )
    session.add(operation)
    session.flush()
    stage_execution(
        session,
        PlatformOperationExecution(
            execution_id=operation.id, workflow_id=f"platform-operation:{operation.id}"
        ),
    )
    record_event(session, actor=actor, operation=operation, action="submitted")
    return operation


def cancel(session: Session, *, operation_id: UUID, actor: CurrentUser) -> PlatformOperation:
    require_platform_admin(actor)
    operation = session.scalar(
        select(PlatformOperation).where(PlatformOperation.id == operation_id).with_for_update()
    )
    if operation is None:
        raise NotFoundError("Platform operation not found")
    if operation.status not in TERMINAL:
        operation.status = "cancelled"
        record_event(session, actor=actor, operation=operation, action="cancelled")
    return operation


def resume(
    session: Session, *, operation_id: UUID, actor: CurrentUser, handlers: OperationHandlers
) -> PlatformOperation:
    require_platform_admin(actor)
    operation = session.scalar(
        select(PlatformOperation).where(PlatformOperation.id == operation_id).with_for_update()
    )
    if operation is None:
        raise NotFoundError("Platform operation not found")
    resolve(handlers, owner=operation.owner, command=operation.command)
    if operation.status not in {"cancelled", "needs_attention"}:
        raise ConflictError("Only cancelled or rejected operations can resume")
    operation.status, operation.failure_code, operation.error_detail = "accepted", None, None
    operation.requested_by_user_id = actor.user_id
    operation.generation += 1
    workflow_id = f"platform-operation:{operation.id}:{operation.generation}"
    stage_execution(
        session,
        PlatformOperationExecution(
            execution_id=operation.id, workflow_id=workflow_id, generation=operation.generation
        ),
        dispatch_id=uuid5(NAMESPACE_URL, workflow_id),
    )
    record_event(session, actor=actor, operation=operation, action="resumed")
    return operation


def record_event(
    session: Session, *, actor: CurrentUser, operation: PlatformOperation, action: str
) -> None:
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action=f"platform.operation_{action}",
            actor=AuditActor.from_user(actor),
            source="platform",
            target_type="platform_operation",
            target_id=str(operation.id),
            summary=f"Platform command {action}",
            payload_json=canonical_json({"owner": operation.owner, "command": operation.command}),
        ),
    )
