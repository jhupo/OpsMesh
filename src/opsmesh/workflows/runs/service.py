"""Workflow run persistence; node effects remain delegated to execution."""

from uuid import UUID, uuid4

from opsmesh_sdk.identity import InvocationSource
from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.bindings import authorize_bindings
from opsmesh.workflows.contracts import PreparedWorkflow
from opsmesh.workflows.instances.access import get_instance
from opsmesh.workflows.runs.events import append_event
from opsmesh.workflows.runs.models import WorkflowNodeRun, WorkflowOperation, WorkflowRun
from opsmesh.workflows.runs.schemas import RunCreate
from opsmesh.workflows.schema import validate_json
from opsmesh.workflows.values import validate_value


def submit_run(
    session: Session,
    *,
    actor: CurrentUser,
    request: RunCreate,
    cipher: SecretCipher,
    source: InvocationSource | None = None,
) -> WorkflowRun:
    instance = get_instance(session, actor=actor, instance_id=request.instance_id, lock=True)
    if instance.status != "active":
        raise ConflictError("The workflow instance is disabled")
    prepared = PreparedWorkflow.model_validate(cipher.decrypt_payload(instance.encrypted_prepared))
    authorize_bindings(session, actor=actor, prepared=prepared)
    validate_value(request.input, prepared.plan.entry.spec.input_schema)
    payload = request.model_dump(mode="json")
    validate_json(payload)
    if len(canonical_json(payload).encode()) > 1_000_000:
        raise InvalidArgumentError("The workflow input exceeds its size limit")
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"workflow-run:{actor.user_id}:{request.request_key}"},
    )
    fingerprint = payload_digest(
        payload if source is None else {**payload, "source": source.model_dump(mode="json")}
    )
    previous = session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.user_id == actor.user_id, WorkflowRun.request_key == request.request_key
        )
    )
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise ConflictError("The workflow request key was reused")
        return previous
    encrypted = cipher.encrypt_payload(payload)
    row = WorkflowRun(
        id=uuid4(),
        user_id=actor.user_id,
        instance_id=instance.id,
        engine=request.engine,
        request_key=request.request_key,
        fingerprint=fingerprint,
        encrypted_input=encrypted.ciphertext,
        encrypted_context=cipher.encrypt_payload(
            (source or InvocationSource()).model_dump(mode="json")
        ).ciphertext,
        key_id=encrypted.key_id,
    )
    session.add(row)
    session.flush()
    append_event(session, run_id=row.id, kind="queued", payload={}, cipher=cipher)
    return row


def get_workflow_run(session: Session, *, run_id: UUID, lock: bool = False) -> WorkflowRun:
    query = select(WorkflowRun).where(WorkflowRun.id == run_id)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise NotFoundError("The workflow run does not exist")
    return row


def finish_run(
    session: Session, *, row: WorkflowRun, status: str, output: object, cipher: SecretCipher
) -> WorkflowRun:
    if row.status not in {"queued", "running", "cancelling"}:
        return row
    if status not in {"completed", "failed", "cancelled"}:
        raise InvalidArgumentError("Invalid terminal workflow status")
    from opsmesh.execution.runs.models import ACTIVE as AGENT_ACTIVE
    from opsmesh.execution.runs.models import ExecutionRun

    uncertain = session.scalar(
        select(
            exists().where(
                WorkflowOperation.run_id == row.id,
                WorkflowOperation.status.in_(("started", "uncertain")),
            )
        )
    )
    linked = session.scalar(
        select(
            exists().where(
                WorkflowNodeRun.run_id == row.id,
                WorkflowNodeRun.execution_run_id == ExecutionRun.id,
                ExecutionRun.status.in_(AGENT_ACTIVE),
            )
        )
    )
    row.status = "needs_attention" if uncertain or linked else status
    row.error_code = (
        "external_effect_unknown"
        if uncertain or linked
        else "workflow_failed"
        if status == "failed"
        else None
    )
    if row.status == "completed":
        validate_json({"output": output}, max_bytes=4_000_000)
        encrypted = cipher.encrypt_payload({"output": output})
        row.encrypted_result, row.key_id = encrypted.ciphertext, encrypted.key_id
    append_event(session, run_id=row.id, kind=row.status, payload={}, cipher=cipher)
    return row
