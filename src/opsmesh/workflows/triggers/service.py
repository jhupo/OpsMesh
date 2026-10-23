"""All trigger effects join the existing run and Outbox transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.execution.instances.access import current_user
from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.workflows.triggers.contracts import TriggerSpec
from opsmesh.workflows.triggers.models import Trigger, TriggerDelivery
from opsmesh.workflows.triggers.ports import TargetPorts

_NO_EVENT = object()


def owned(session: Session, actor: CurrentUser, trigger_id: UUID, *, lock: bool = False) -> Trigger:
    row = session.get(Trigger, trigger_id, with_for_update=lock)
    if row is None or row.owner_user_id != actor.user_id:
        raise NotFoundError("The trigger does not exist")
    return row


def specification(row: Trigger, cipher: SecretCipher) -> TriggerSpec:
    return TriggerSpec.model_validate(cipher.decrypt_payload(row.encrypted_spec, key_id=row.key_id))


def audit(session: Session, actor: CurrentUser, action: str, trigger_id: UUID) -> None:
    record_audit(
        session,
        AuditRecord(
            category="triggers",
            action=action,
            actor=AuditActor.from_user(actor),
            source="platform",
            target_type="trigger",
            target_id=str(trigger_id),
        ),
    )


def save(
    session: Session,
    *,
    actor: CurrentUser,
    spec: TriggerSpec,
    cipher: SecretCipher,
    ports: TargetPorts,
    expected_revision: int,
    trigger_id: UUID | None = None,
    secret: str | None = None,
) -> Trigger:
    actor = current_user(session, actor.user_id)
    ports.authorize(session, actor, spec.target_kind, spec.target_id)
    row = owned(session, actor, trigger_id, lock=True) if trigger_id is not None else None
    if expected_revision != (row.revision if row else 0):
        raise ConflictError("The trigger revision changed")
    if row and (row.kind != spec.kind or row.status == "deleted"):
        raise ConflictError("A trigger kind is immutable and deleted triggers cannot be edited")
    payload = spec.model_dump(mode="json")
    if len(canonical_json(payload).encode()) > 1_000_000:
        raise InvalidArgumentError("The trigger input exceeds its size limit")
    encrypted = cipher.encrypt_payload(payload)
    if row is None:
        if spec.kind == "webhook" and secret is None:
            raise InvalidArgumentError("A webhook requires a signing secret")
        row = Trigger(
            owner_user_id=actor.user_id,
            kind=spec.kind,
            revision=1,
            applied_revision=0,
            status="active",
        )
        session.add(row)
    else:
        row.revision += 1
    row.encrypted_spec, row.key_id = encrypted.ciphertext, encrypted.key_id
    if secret is not None:
        if spec.kind != "webhook" or not 32 <= len(secret.encode()) <= 4096:
            raise InvalidArgumentError("Webhook signing secrets require 32 to 4096 bytes")
        row.encrypted_secret = cipher.encrypt_payload({"secret": secret}).ciphertext
    if spec.kind == "webhook":
        row.applied_revision = row.revision
    row.sync_error = None
    session.flush()
    audit(session, actor, "configuration.save", row.id)
    return row


def fire(
    session: Session,
    *,
    row: Trigger,
    event_id: str,
    event: object = _NO_EVENT,
    cipher: SecretCipher,
    ports: TargetPorts,
    expected_revision: int | None = None,
) -> TriggerDelivery:
    actor = current_user(session, row.owner_user_id)
    if row.status != "active":
        raise ConflictError("The trigger is deleted")
    spec = specification(row, cipher)
    if not spec.enabled or (expected_revision is not None and row.revision != expected_revision):
        raise ConflictError("The trigger is disabled or its schedule changed")
    ports.authorize(session, actor, spec.target_kind, spec.target_id)
    has_event = event is not _NO_EVENT
    value = {"input": spec.input, "event": event} if has_event else spec.input
    fingerprint = payload_digest({"has_event": has_event, "event": event if has_event else None})
    previous = session.scalar(
        select(TriggerDelivery).where(
            TriggerDelivery.trigger_id == row.id, TriggerDelivery.event_id == event_id
        )
    )
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise ConflictError("The event identity was reused with different input")
        return previous
    receipt = TriggerDelivery(
        trigger_id=row.id,
        event_id=event_id,
        revision=row.revision,
        fingerprint=fingerprint,
        target_kind=spec.target_kind,
        run_id=ports.submit(
            session,
            actor,
            spec.target_kind,
            spec.target_id,
            value,
            "trigger:" + payload_digest({"trigger_id": str(row.id), "event_id": event_id}),
        ),
    )
    session.add(receipt)
    session.flush()
    audit(session, actor, "delivery.submit", row.id)
    return receipt
