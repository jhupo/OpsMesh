"""Persist explicit cancellation, addressed signals and node approvals."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.contracts import PreparedWorkflow, WaitNode
from opsmesh.workflows.instances.models import WorkflowInstance
from opsmesh.workflows.runs.access import get_run
from opsmesh.workflows.runs.events import append_event
from opsmesh.workflows.runs.models import WorkflowNodeRun, WorkflowSignal
from opsmesh.workflows.runs.nodes import resolve_node
from opsmesh.workflows.runs.schemas import ApprovalDecision, SignalCreate
from opsmesh.workflows.schema import validate_json


def cancel_run(session: Session, *, actor: CurrentUser, run_id: UUID, cipher: SecretCipher) -> None:
    row = get_run(session, actor=actor, run_id=run_id, lock=True)
    if row.status in {"queued", "running"}:
        row.status = "cancelling"
        append_event(session, run_id=row.id, kind="cancelling", payload={}, cipher=cipher)


def deliver_signal(
    session: Session,
    *,
    actor: CurrentUser,
    run_id: UUID,
    request: SignalCreate,
    cipher: SecretCipher,
) -> WorkflowSignal:
    row = get_run(session, actor=actor, run_id=run_id, lock=True)
    payload = request.model_dump(mode="json")
    validate_json(payload)
    fingerprint = payload_digest(payload)
    previous = session.scalar(
        select(WorkflowSignal).where(
            WorkflowSignal.run_id == run_id,
            WorkflowSignal.path == request.path,
            WorkflowSignal.name == request.name,
        )
    )
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise ConflictError("This addressed signal already has another value")
        return previous
    if row.status not in {"queued", "running"}:
        raise ConflictError("The workflow is not accepting signals")
    instance = session.get(WorkflowInstance, row.instance_id)
    assert instance is not None
    prepared = PreparedWorkflow.model_validate(cipher.decrypt_payload(instance.encrypted_prepared))
    node = resolve_node(prepared, request.path)
    if (
        not isinstance(node, WaitNode)
        or node.wait_kind != "signal"
        or node.signal_name != request.name
    ):
        raise InvalidArgumentError("The signal does not address a wait node")
    encrypted = cipher.encrypt_payload({"value": request.value})
    signal = WorkflowSignal(
        id=uuid4(),
        run_id=run_id,
        path=request.path,
        name=request.name,
        request_key=request.request_key,
        fingerprint=fingerprint,
        encrypted_payload=encrypted.ciphertext,
    )
    session.add(signal)
    session.flush()
    append_event(
        session,
        run_id=run_id,
        kind="signal_received",
        payload={"path": request.path, "name": request.name},
        cipher=cipher,
    )
    return signal


def decide_approval(
    session: Session,
    *,
    actor: CurrentUser,
    run_id: UUID,
    node_id: UUID,
    request: ApprovalDecision,
    cipher: SecretCipher,
) -> WorkflowNodeRun:
    row = get_run(session, actor=actor, run_id=run_id, lock=True)
    node = session.get(WorkflowNodeRun, node_id, with_for_update=True)
    if node is None or node.run_id != row.id:
        raise NotFoundError("The workflow node does not exist")
    if node.approval_id != request.approval_id:
        raise ConflictError("This approval is stale")
    if node.approval_decision is not None:
        if node.approval_decision != request.allow:
            raise ConflictError("This approval already has another decision")
        return node
    if row.status != "running" or node.status != "awaiting_approval":
        raise ConflictError("The node is not awaiting approval")
    node.approval_decision = request.allow
    append_event(
        session,
        run_id=run_id,
        kind="node_approval",
        payload={"node_id": str(node.id), "allow": request.allow},
        cipher=cipher,
    )
    return node
