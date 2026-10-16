"""Audit service: transactional idempotent append, chain advance, query, verify."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.governance.audit.contracts import AuditVerificationExecution
from opsmesh.governance.audit.hashing import chain_digest, payload_digest
from opsmesh.governance.audit.models import (
    AuditAppendReceipt,
    AuditChainHead,
    AuditEvent,
    AuditVerification,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.temporal.dispatch.outbox import stage_execution

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
CHAIN_HEAD_ID = 1

AuditSource = Literal["platform", "extension_reported"]
ActorType = Literal["user", "platform", "delegation"]


class AuditSubject(Protocol):
    """The verified subject one audit fact is attributed to.

    Identity owns the concrete value; governance only requires the stable
    identifier so the dependency direction stays identity -> governance.
    """

    @property
    def user_id(self) -> UUID: ...


@dataclass(frozen=True)
class AuditActor:
    """The verified actor and scope of one append; never taken from the body."""

    actor_type: ActorType
    actor_id: str | None = None
    workspace_id: UUID | None = None
    project_id: UUID | None = None
    installation_id: UUID | None = None
    instance_id: UUID | None = None
    invocation_id: UUID | None = None
    package_key: str | None = None
    configuration_revision: str | None = None

    @classmethod
    def from_user(cls, user: AuditSubject) -> AuditActor:
        return cls(actor_type="user", actor_id=str(user.user_id))


def append_platform_event(
    session: Session,
    *,
    actor: AuditActor,
    action: str,
    target_type: str,
    target_id: str,
    summary: str,
    details: dict[str, object] | None = None,
) -> AuditEvent:
    """Append one platform fact inside the caller's transaction."""
    return _append_event(
        session,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        details=details or {},
        evidence_refs=[],
        source="platform",
    )


def record_extension_event(
    session: Session,
    *,
    actor: AuditActor,
    action: str,
    target_type: str,
    target_id: str,
    summary: str,
    details: dict[str, object] | None,
    evidence_refs: list[str],
    idempotency_key: str,
) -> tuple[AuditAppendReceipt, bool]:
    """Append one extension-reported fact; identical retries reuse the receipt.

    Returns the receipt and whether this call replayed an earlier append. A
    reused key with a different declared input is a conflict, never a silent
    second event.
    """
    if actor.installation_id is None or actor.invocation_id is None:
        raise InvalidArgumentError("An extension append requires its invocation scope")
    declared = {
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "summary": summary,
        "details": details or {},
        "evidence_refs": list(evidence_refs),
    }
    scope = f"installation:{actor.installation_id}:invocation:{actor.invocation_id}"
    input_digest = payload_digest(declared)
    # The chain head lock is the serialization boundary of one append: the
    # receipt is read under it so two concurrent appends of one key cannot
    # both pass the lookup and race toward the unique constraint. The flush
    # first makes an earlier append in this same transaction visible to the
    # lookup, because sessions run with autoflush=False.
    head = _chain_head(session)
    session.flush()
    existing = session.scalar(
        select(AuditAppendReceipt).where(
            AuditAppendReceipt.idempotency_scope == scope,
            AuditAppendReceipt.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.input_digest != input_digest:
            raise ConflictError(
                "The idempotency key was reused with a different audit input"
            )
        return existing, True
    event = _append_event(
        session,
        head=head,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        details=details or {},
        evidence_refs=evidence_refs,
        source="extension_reported",
    )
    session.flush()
    receipt = AuditAppendReceipt(
        idempotency_scope=scope,
        idempotency_key=idempotency_key,
        input_digest=input_digest,
        event_id=event.id,
    )
    session.add(receipt)
    return receipt, False


def get_event(session: Session, event_id: UUID) -> AuditEvent:
    event = session.get(AuditEvent, event_id)
    if event is None:
        raise NotFoundError("The audit event does not exist")
    return event


def list_events(
    session: Session,
    *,
    after_sequence: int | None = None,
    actor_type: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    workspace_id: UUID | None = None,
    project_id: UUID | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[AuditEvent]:
    """Cursor pagination by chain sequence; filters are ANDed server-side."""
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise InvalidArgumentError(f"The audit page limit must be between 1 and {MAX_LIST_LIMIT}")
    query = select(AuditEvent).order_by(AuditEvent.sequence.asc())
    if after_sequence is not None:
        query = query.where(AuditEvent.sequence > after_sequence)
    if actor_type is not None:
        query = query.where(AuditEvent.actor_type == actor_type)
    if action is not None:
        query = query.where(AuditEvent.action == action)
    if target_type is not None:
        query = query.where(AuditEvent.target_type == target_type)
    if target_id is not None:
        query = query.where(AuditEvent.target_id == target_id)
    if workspace_id is not None:
        query = query.where(AuditEvent.workspace_id == workspace_id)
    if project_id is not None:
        query = query.where(AuditEvent.project_id == project_id)
    return list(session.scalars(query.limit(limit)))


def start_verification(
    session: Session,
    *,
    requested_by: AuditSubject,
    from_sequence: int,
    to_sequence: int,
) -> AuditVerification:
    """Register one verification run and stage its durable execution."""
    if from_sequence < 1 or to_sequence < from_sequence:
        raise InvalidArgumentError("The verification range is not usable")
    head = _chain_head(session)
    if to_sequence > head.last_sequence:
        raise InvalidArgumentError("The verification range exceeds the chain head")
    verification_id = uuid4()
    verification = AuditVerification(
        id=verification_id,
        requested_by_user_id=requested_by.user_id,
        workflow_id=f"audit-verification:{verification_id}",
        status="running",
        from_sequence=from_sequence,
        to_sequence=to_sequence,
    )
    session.add(verification)
    stage_execution(
        session,
        AuditVerificationExecution(
            execution_id=verification_id,
            workflow_id=verification.workflow_id,
            verification_id=verification_id,
            from_sequence=from_sequence,
            to_sequence=to_sequence,
        ),
    )
    return verification


def get_verification(session: Session, verification_id: UUID) -> AuditVerification:
    verification = session.get(AuditVerification, verification_id)
    if verification is None:
        raise NotFoundError("The audit verification does not exist")
    return verification


def record_verification_progress(
    session: Session, *, verification_id: UUID, checked_count: int
) -> None:
    verification = session.get(AuditVerification, verification_id)
    if verification is None:
        raise NotFoundError("The audit verification does not exist")
    verification.checked_count = checked_count


def complete_verification(
    session: Session,
    *,
    verification_id: UUID,
    status: Literal["succeeded", "failed", "cancelled"],
    first_broken_sequence: int | None = None,
    failure_reason: str | None = None,
) -> None:
    verification = session.get(AuditVerification, verification_id)
    if verification is None:
        raise NotFoundError("The audit verification does not exist")
    verification.status = status
    verification.first_broken_sequence = first_broken_sequence
    verification.failure_reason = failure_reason


def _chain_head(session: Session) -> AuditChainHead:
    """Lock the singleton head, creating it on a chain that never started.

    The migration seeds the row; a database built by ``create_all`` (tests)
    gets it here. Creation runs under a transaction-scoped advisory lock:
    without it two concurrent first appends both miss the row and race toward
    the primary key, and the loser surfaces an IntegrityError instead of the
    winner's receipt.
    """
    head = session.scalar(
        select(AuditChainHead).where(AuditChainHead.id == CHAIN_HEAD_ID).with_for_update()
    )
    if head is not None:
        return head
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"audit-chain-head:{CHAIN_HEAD_ID}"},
    )
    head = session.scalar(
        select(AuditChainHead).where(AuditChainHead.id == CHAIN_HEAD_ID).with_for_update()
    )
    if head is not None:
        return head
    head = AuditChainHead(id=CHAIN_HEAD_ID)
    session.add(head)
    session.flush()
    return head


def _append_event(
    session: Session,
    *,
    actor: AuditActor,
    action: str,
    target_type: str,
    target_id: str,
    summary: str,
    details: dict[str, object],
    evidence_refs: list[str],
    source: AuditSource,
    head: AuditChainHead | None = None,
) -> AuditEvent:
    """Advance the chain under the head row lock and append one event.

    ``head`` lets a caller that already holds the lock (the extension append
    path) reuse it instead of taking it a second time.
    """
    locked_head = head if head is not None else _chain_head(session)
    event = AuditEvent(
        sequence=locked_head.last_sequence + 1,
        occurred_at=datetime.now(UTC),
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        workspace_id=actor.workspace_id,
        project_id=actor.project_id,
        installation_id=actor.installation_id,
        instance_id=actor.instance_id,
        invocation_id=actor.invocation_id,
        package_key=actor.package_key,
        configuration_revision=actor.configuration_revision,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        details=details,
        evidence_refs=list(evidence_refs),
        source=source,
        previous_hash=locked_head.last_hash,
        event_hash="",
    )
    event.event_hash = chain_digest(locked_head.last_hash, event.payload_bytes())
    session.add(event)
    locked_head.last_sequence = event.sequence
    locked_head.last_hash = event.event_hash
    locked_head.updated_at = datetime.now(UTC)
    return event
