"""Persistent workflow instance creation and explicit lifecycle control."""

from uuid import UUID

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.redaction import RedactionRules, redact_sensitive_payload
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.access import current_actor
from opsmesh.workflows.bindings import bind_workflow
from opsmesh.workflows.instances.access import get_instance
from opsmesh.workflows.instances.models import WorkflowInstance
from opsmesh.workflows.instances.schemas import InstanceCreate
from opsmesh.workflows.runs.models import ACTIVE, WorkflowRun
from opsmesh.workflows.schema import validate_json


def create_instance(
    session: Session,
    *,
    actor: CurrentUser,
    request: InstanceCreate,
    cipher: SecretCipher,
    redaction: RedactionRules,
) -> WorkflowInstance:
    actor = current_actor(session, actor)
    payload = request.model_dump(mode="json")
    validate_json(payload)
    for binding in request.bindings.tools.values():
        if redact_sensitive_payload(binding.config, rules=redaction) != binding.config:
            raise InvalidArgumentError("Workflow bindings must use credential references")
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"workflow-instance:{actor.user_id}:{request.request_key}"},
    )
    fingerprint = payload_digest(payload)
    previous = session.scalar(
        select(WorkflowInstance).where(
            WorkflowInstance.owner_user_id == actor.user_id,
            WorkflowInstance.request_key == request.request_key,
        )
    )
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise ConflictError("The instance request key was reused with different input")
        return previous
    prepared = bind_workflow(
        session, actor=actor, revision_id=request.revision_id, bindings=request.bindings
    )
    frozen = prepared.model_dump(mode="json")
    validate_json(frozen, max_bytes=4_000_000)
    encrypted = cipher.encrypt_payload(frozen)
    row = WorkflowInstance(
        owner_user_id=actor.user_id,
        revision_id=request.revision_id,
        name=request.name,
        request_key=request.request_key,
        fingerprint=fingerprint,
        encrypted_prepared=encrypted.ciphertext,
        key_id=encrypted.key_id,
    )
    session.add(row)
    session.flush()
    return row


def set_status(
    session: Session, *, actor: CurrentUser, instance_id: UUID, status: str
) -> WorkflowInstance:
    if status not in {"active", "disabled", "deleted"}:
        raise InvalidArgumentError("Invalid workflow instance status")
    row = get_instance(session, actor=actor, instance_id=instance_id, lock=True)
    if session.scalar(
        select(exists().where(WorkflowRun.instance_id == row.id, WorkflowRun.status.in_(ACTIVE)))
    ):
        raise ConflictError("The workflow instance has unfinished runs")
    row.status = status
    return row
