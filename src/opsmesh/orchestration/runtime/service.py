"""Stage explicit environment lifecycle and sandbox commands with durable receipts."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.contracts import RuntimeCommand
from opsmesh.orchestration.runtime.models import Environment, EnvironmentOperationReceipt
from opsmesh.orchestration.runtime.schemas import EnvironmentOperationRequest
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.serialization import payload_digest
from opsmesh.temporal.dispatch.outbox import stage_execution


def create_environment(
    session: Session,
    *,
    subject: ExecutionSubject,
    scope: ExecutionScope,
    admission: ScopeAdmission,
    image: str,
    allowed_images: frozenset[str],
    operation_id: str,
    root_directory_id: UUID | None = None,
) -> Environment:
    admission.admit(session, subject=subject, scope=scope)
    if image not in allowed_images:
        raise InvalidArgumentError("The sandbox image is not approved by the platform")
    scope_id = scope.configuration_id or subject.user_id
    previous = session.get(EnvironmentOperationReceipt, (scope_id, operation_id))
    fingerprint = payload_digest(
        {
            "action": "create",
            "image": image,
            "principal_id": str(subject.user_id),
            "configuration_id": str(scope.configuration_id),
            "root_directory_id": str(root_directory_id),
        }
    )
    if previous is not None:
        record = load_environment(
            session, environment_id=previous.environment_id, subject=subject, admission=admission
        )
        if previous.fingerprint != fingerprint:
            raise ConflictError("Operation identity was reused with different input")
        return record
    owner_ref = (
        f"configuration:{scope.configuration_id}"
        if scope.configuration_id
        else f"user:{subject.user_id}"
    )

    def create() -> Environment:
        record = Environment(
            scope_id=scope_id,
            principal_id=subject.user_id,
            owner_ref=owner_ref,
            status="pending",
            configuration={
                "image": image,
                "root_directory_id": str(root_directory_id) if root_directory_id else None,
            },
        )
        session.add(record)
        session.flush()
        stage_operation(
            session,
            record=record,
            subject=subject,
            operation_id=operation_id,
            action="create",
            arguments={"image": image},
            fingerprint=fingerprint,
        )
        return record

    return run_idempotent_create(
        session=session,
        scope_id=scope_id,
        actor_id=subject.user_id,
        operation="orchestration.environment",
        idempotency_key=operation_id,
        request_payload={"fingerprint": fingerprint},
        get_existing=lambda identifier: load_environment(
            session, environment_id=identifier, subject=subject, admission=admission
        ),
        create=create,
        resource_id=lambda record: record.id,
    )


def submit_operation(
    session: Session,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    environment_id: UUID,
    operation_id: str,
    request: EnvironmentOperationRequest,
) -> EnvironmentOperationReceipt:
    record = load_environment(
        session, environment_id=environment_id, subject=subject, admission=admission, lock=True
    )
    arguments = request.model_dump(mode="json", exclude={"action"}, exclude_none=True)
    fingerprint = payload_digest({"action": request.action, "arguments": arguments})
    previous = session.get(EnvironmentOperationReceipt, (record.scope_id, operation_id))
    if previous is not None:
        if previous.environment_id != record.id or previous.fingerprint != fingerprint:
            raise ConflictError("Operation identity was reused with different input")
        return previous
    if record.status != "ready":
        raise ConflictError("The environment is not ready")
    if record.active_operation_id is not None:
        raise ConflictError("The environment has an unfinished operation")
    if request.action == "command" and not request.command:
        raise InvalidArgumentError("A command requires a non-empty argument vector")
    if request.action in {"read", "write"} and request.path is None:
        raise InvalidArgumentError("A file operation requires a relative path")
    if request.action == "write" and request.content_base64 is None:
        raise InvalidArgumentError("A file write requires content")
    return stage_operation(
        session,
        record=record,
        subject=subject,
        operation_id=operation_id,
        action=request.action,
        arguments=arguments,
        fingerprint=fingerprint,
    )


def stage_operation(
    session: Session,
    *,
    record: Environment,
    subject: ExecutionSubject,
    operation_id: str,
    action: str,
    arguments: dict[str, object],
    fingerprint: str,
    dispatch: bool = True,
    run_id: UUID | None = None,
    cleanup_ref: UUID | None = None,
) -> EnvironmentOperationReceipt:
    pending = session.scalar(
        select(EnvironmentOperationReceipt.operation_id)
        .where(
            EnvironmentOperationReceipt.environment_id == record.id,
            EnvironmentOperationReceipt.status.in_(("pending", "started", "uncertain")),
        )
        .limit(1)
    )
    if pending is not None:
        raise ConflictError("The environment has an unfinished operation")
    execution_id = uuid4()
    receipt = EnvironmentOperationReceipt(
        scope_id=record.scope_id,
        operation_id=operation_id,
        environment_id=record.id,
        action=action,
        fingerprint=fingerprint,
        status="pending",
        run_id=run_id,
        workflow_id=f"runtime:{execution_id}" if dispatch else None,
    )
    session.add(receipt)
    record.active_operation_id = operation_id
    session.flush()
    if not dispatch:
        return receipt
    stage_execution(
        session,
        RuntimeCommand(
            execution_id=execution_id,
            workflow_id=f"runtime:{execution_id}",
            environment_id=record.id,
            principal_id=subject.user_id,
            operation_id=operation_id,
            action=action,
            arguments=arguments,
            cleanup_ref=cleanup_ref,
        ),
    )
    return receipt
