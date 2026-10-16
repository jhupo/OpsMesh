"""S4 governance tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for governance: the serialized audit
chain, idempotent extension appends, tamper detection and the single-decision
approval contract.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.governance.approvals import service as approvals
from opsmesh.governance.approvals.targets import (
    ApprovalTarget,
)
from opsmesh.governance.audit import service as audit
from opsmesh.governance.audit.hashing import first_broken_sequence
from opsmesh.governance.audit.models import (
    AuditAppendReceipt,
    AuditEvent,
    AuditVerification,
)
from opsmesh.shared.errors import (
    ConflictError,
    ForbiddenError,
    InvalidArgumentError,
    NotFoundError,
)
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry

INSTALLATION_ID = UUID("11111111-2222-3333-4444-555555555555")
INSTANCE_ID = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
INVOCATION_ID = UUID("bbbbbbbb-cccc-dddd-eeee-ffff00001111")
USER_ID = UUID("99999999-8888-7777-6666-555555555555")
OTHER_USER_ID = UUID("12121212-3434-5656-7878-909090909090")


class Subject:
    """The verified deciding subject; identity owns the real value."""

    def __init__(self, user_id: UUID, *, admin: bool = False) -> None:
        self.user_id = user_id
        self._admin = admin

    def has_platform_role(self, role: str) -> bool:
        return self._admin and role == "admin"


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def notify_decided(
        self,
        session: Session,
        *,
        approval_id: UUID,
        decision: str,
        target: ApprovalTarget,
        decided_by_user_id: UUID,
        reason: str | None,
    ) -> None:
        self.calls.append({"approval_id": approval_id, "decision": decision})


def extension_actor() -> audit.AuditActor:
    return audit.AuditActor(
        actor_type="delegation",
        actor_id=str(USER_ID),
        installation_id=INSTALLATION_ID,
        instance_id=INSTANCE_ID,
        invocation_id=INVOCATION_ID,
        package_key="demo",
    )


def append_two_events(database_sessions) -> None:
    with database_sessions() as session, session.begin():
        audit.append_platform_event(
            session,
            actor=audit.AuditActor(actor_type="platform"),
            action="user.created",
            target_type="user",
            target_id="user-1",
            summary="Account opened",
        )
        audit.append_platform_event(
            session,
            actor=audit.AuditActor(actor_type="user", actor_id=str(USER_ID)),
            action="user.profile_updated",
            target_type="user",
            target_id="user-1",
            summary="Profile updated",
            details={"changed": ["display_name"]},
        )


def load_chain(database_sessions) -> list[AuditEvent]:
    with database_sessions() as session:
        return list(session.scalars(select(AuditEvent).order_by(AuditEvent.sequence.asc())))


def test_the_chain_links_every_event_to_its_predecessor(database_sessions) -> None:
    append_two_events(database_sessions)
    events = load_chain(database_sessions)
    assert [event.sequence for event in events] == [1, 2]
    assert events[0].previous_hash == "0" * 64
    assert events[1].previous_hash == events[0].event_hash
    assert first_broken_sequence(events) is None


def test_a_post_append_edit_breaks_the_chain(database_sessions) -> None:
    append_two_events(database_sessions)
    with database_sessions() as session, session.begin():
        session.execute(
            update(AuditEvent)
            .where(AuditEvent.sequence == 1)
            .values(summary="Rewritten after append")
        )
    events = load_chain(database_sessions)
    assert first_broken_sequence(events) == 1


def test_an_extension_append_is_idempotent_per_invocation(database_sessions) -> None:
    actor = extension_actor()
    with database_sessions() as session, session.begin():
        first, replayed = audit.record_extension_event(
            session,
            actor=actor,
            action="invocation.completed",
            target_type="invocation",
            target_id="target-1",
            summary="Invocation finished",
            details={"duration_ms": 12},
            evidence_refs=["run:abc"],
            idempotency_key="audit-key-0001",
        )
        assert replayed is False
        again, replayed_again = audit.record_extension_event(
            session,
            actor=actor,
            action="invocation.completed",
            target_type="invocation",
            target_id="target-1",
            summary="Invocation finished",
            details={"duration_ms": 12},
            evidence_refs=["run:abc"],
            idempotency_key="audit-key-0001",
        )
        assert replayed_again is True
        assert again.event_id == first.event_id
    with database_sessions() as session:
        events = list(session.scalars(select(AuditEvent)))
    assert len(events) == 1
    assert events[0].source == "extension_reported"
    assert events[0].evidence_refs == ["run:abc"]


def test_a_reused_audit_key_with_another_input_conflicts(database_sessions) -> None:
    actor = extension_actor()
    with database_sessions() as session, session.begin():
        audit.record_extension_event(
            session,
            actor=actor,
            action="invocation.completed",
            target_type="invocation",
            target_id="target-1",
            summary="Invocation finished",
            details=None,
            evidence_refs=[],
            idempotency_key="audit-key-0001",
        )
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        audit.record_extension_event(
            session,
            actor=actor,
            action="invocation.completed",
            target_type="invocation",
            target_id="target-1",
            summary="A different summary",
            details=None,
            evidence_refs=[],
            idempotency_key="audit-key-0001",
        )


def test_an_extension_append_requires_its_invocation_scope(database_sessions) -> None:
    with database_sessions() as session, session.begin(), pytest.raises(InvalidArgumentError):
        audit.record_extension_event(
            session,
            actor=audit.AuditActor(actor_type="delegation", actor_id=str(USER_ID)),
            action="invocation.completed",
            target_type="invocation",
            target_id="target-1",
            summary="Invocation finished",
            details=None,
            evidence_refs=[],
            idempotency_key="audit-key-0002",
        )


def test_a_verification_run_is_staged_as_one_durable_execution(database_sessions) -> None:
    append_two_events(database_sessions)
    subject = Subject(USER_ID, admin=True)
    with database_sessions() as session, session.begin():
        verification = audit.start_verification(
            session, requested_by=subject, from_sequence=1, to_sequence=2
        )
        staged = list(session.scalars(select(TemporalOutboxEntry)))
    assert verification.status == "running"
    assert len(staged) == 1
    assert staged[0].workflow_id == f"audit-verification:{verification.id}"
    with database_sessions() as session:
        loaded = session.get(AuditVerification, verification.id)
        assert loaded is not None and loaded.to_sequence == 2


def test_a_verification_range_beyond_the_head_is_refused(database_sessions) -> None:
    append_two_events(database_sessions)
    subject = Subject(USER_ID, admin=True)
    with database_sessions() as session, session.begin(), pytest.raises(InvalidArgumentError):
        audit.start_verification(
            session, requested_by=subject, from_sequence=1, to_sequence=99
        )


GOVERNANCE_CONCURRENCY_TABLES = (
    "temporal_dispatches",
    "http_creation_receipts",
    "audit_append_receipts",
    "audit_chain_head",
    "audit_events",
    "audit_verifications",
)


def reset_governance_tables(engine) -> None:
    """Start one concurrency test from a known-empty audit state."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(GOVERNANCE_CONCURRENCY_TABLES)))


@pytest.fixture
def clean_concurrency_tables(database_engine):
    """Empty the database around a concurrency test, before and after it."""
    reset_governance_tables(database_engine)
    yield
    reset_governance_tables(database_engine)


def test_concurrent_extension_appends_of_one_key_replay_one_event(
    database_engine, clean_concurrency_tables
) -> None:
    """The receipt is decided under the chain head lock, never after it.

    Two concurrent appends of one idempotency key both find no receipt before
    either lock; the loser must read the winner's committed receipt instead of
    appending a second event that only the unique constraint would stop.
    """
    sessions = sessionmaker(bind=database_engine, autoflush=False, expire_on_commit=False)
    race_key = f"audit-key-race-{uuid4()}"
    race_target = f"target-race-{uuid4()}"
    start = threading.Event()
    receipts: dict[str, object] = {}
    replays: dict[str, bool] = {}
    errors: dict[str, str] = {}

    def append(name: str) -> None:
        start.wait(timeout=10)
        try:
            with sessions() as session, session.begin():
                receipt, replayed = audit.record_extension_event(
                    session,
                    actor=extension_actor(),
                    action="invocation.completed",
                    target_type="invocation",
                    target_id=race_target,
                    summary="Invocation finished",
                    details={},
                    evidence_refs=[],
                    idempotency_key=race_key,
                )
            receipts[name] = receipt.id
            replays[name] = replayed
        except Exception as exc:  # noqa: BLE001 - the race must not surface one
            errors[name] = type(exc).__name__

    threads = [
        threading.Thread(target=append, args=("first",)),
        threading.Thread(target=append, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == {}
    assert receipts["first"] == receipts["second"]
    assert sorted(replays.values()) == [False, True]
    with sessions() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.target_id == race_target)
            )
        )
        receipt_rows = list(
            session.scalars(
                select(AuditAppendReceipt).where(AuditAppendReceipt.idempotency_key == race_key)
            )
        )
    assert len(events) == 1
    assert len(receipt_rows) == 1


def test_only_the_assigned_approver_decides_once(database_sessions) -> None:
    approver = Subject(USER_ID)
    other = Subject(OTHER_USER_ID)
    notifier = RecordingNotifier()
    with database_sessions() as session, session.begin():
        request = approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(
                target_type="capability_version", target_id="target-1"
            ),
            title="Publish expert",
            summary="Publish one expert version",
            details={"version": "1.0.0"},
            approver_user_id=USER_ID,
        )
        approval_id = request.id
    with database_sessions() as session, session.begin(), pytest.raises(ForbiddenError):
        approvals.decide(
            session,
            approval_id=approval_id,
            decision="approved",
            decided_by=other,
            reason="not mine",
            idempotency_key="decision-key-01",
            notifier=notifier,
        )
    with database_sessions() as session, session.begin():
        decided = approvals.decide(
            session,
            approval_id=approval_id,
            decision="approved",
            decided_by=approver,
            reason="looks good",
            idempotency_key="decision-key-01",
            notifier=notifier,
        )
        assert decided.status == "approved"
        replayed = approvals.decide(
            session,
            approval_id=approval_id,
            decision="approved",
            decided_by=approver,
            reason="looks good",
            idempotency_key="decision-key-01",
            notifier=notifier,
        )
        assert replayed.id == decided.id
        with pytest.raises(ConflictError):
            approvals.decide(
                session,
                approval_id=approval_id,
                decision="rejected",
                decided_by=approver,
                reason="changed my mind",
                idempotency_key="decision-key-02",
                notifier=notifier,
            )
    assert notifier.calls == [{"approval_id": approval_id, "decision": "approved"}]


def test_an_unrelated_replay_of_a_decision_is_refused(database_sessions) -> None:
    """The idempotency key is not a read credential for another subject."""
    approver = Subject(USER_ID)
    stranger = Subject(OTHER_USER_ID)
    notifier = RecordingNotifier()
    with database_sessions() as session, session.begin():
        request = approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(
                target_type="capability_version", target_id="target-1"
            ),
            title="Publish expert",
            summary="Publish one expert version",
            details={"version": "1.0.0"},
            approver_user_id=USER_ID,
        )
        approval_id = request.id
    with database_sessions() as session, session.begin():
        approvals.decide(
            session,
            approval_id=approval_id,
            decision="approved",
            decided_by=approver,
            reason="looks good",
            idempotency_key="decision-key-01",
            notifier=notifier,
        )
    with database_sessions() as session, session.begin(), pytest.raises(ForbiddenError):
        approvals.decide(
            session,
            approval_id=approval_id,
            decision="approved",
            decided_by=stranger,
            reason="not mine",
            idempotency_key="decision-key-01",
            notifier=notifier,
        )


def test_the_same_approval_target_replays_its_request(database_sessions) -> None:
    with database_sessions() as session, session.begin():
        first = approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(
                target_type="capability_version", target_id="target-1"
            ),
            title="Publish expert",
            summary="Publish one expert version",
            details={"version": "1.0.0"},
            approver_user_id=USER_ID,
        )
        replay = approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(
                target_type="capability_version", target_id="target-1"
            ),
            title="Publish expert",
            summary="Publish one expert version",
            details={"version": "1.0.0"},
            approver_user_id=USER_ID,
        )
        assert replay.id == first.id
        with pytest.raises(ConflictError):
            approvals.create_request(
                session,
                kind="capability.publish",
                target=ApprovalTarget(
                    target_type="capability_version", target_id="target-1"
                ),
                title="Another title",
                summary="Publish one expert version",
                details={"version": "1.0.0"},
                approver_user_id=USER_ID,
            )


def test_an_expired_approval_cannot_be_decided(database_sessions) -> None:
    approver = Subject(USER_ID)
    notifier = RecordingNotifier()
    with database_sessions() as session, session.begin():
        request = approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(
                target_type="capability_version", target_id="target-2"
            ),
            title="Publish expert",
            summary="Publish one expert version",
            details={},
            approver_user_id=USER_ID,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        approval_id = request.id
        assert approvals.effective_status(request) == "expired"
    with database_sessions() as session, session.begin(), pytest.raises(ConflictError):
        approvals.decide(
            session,
            approval_id=approval_id,
            decision="approved",
            decided_by=approver,
            reason=None,
            idempotency_key="decision-key-03",
            notifier=notifier,
        )
    assert notifier.calls == []


def test_an_unknown_approval_is_not_found(database_sessions) -> None:
    with database_sessions() as session, pytest.raises(NotFoundError):
        approvals.get_request(session, uuid4())


def test_the_inbox_lists_only_the_assigned_requests(database_sessions) -> None:
    with database_sessions() as session, session.begin():
        approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(
                target_type="capability_version", target_id="target-1"
            ),
            title="Publish expert",
            summary="Publish one expert version",
            details={},
            approver_user_id=USER_ID,
        )
        approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(
                target_type="capability_version", target_id="target-2"
            ),
            title="Publish team",
            summary="Publish one team version",
            details={},
            approver_user_id=OTHER_USER_ID,
        )
    from opsmesh.shared.pagination import PageParams

    with database_sessions() as session:
        rows, total = approvals.list_inbox(
            session, approver_user_id=USER_ID, page=PageParams(limit=10, offset=0)
        )
    assert total == 1
    assert rows[0].target_id == "target-1"


def test_the_chain_head_advances_with_every_append(database_sessions) -> None:
    append_two_events(database_sessions)
    events = load_chain(database_sessions)
    assert len(events) == 2
    assert (
        first_broken_sequence(
            events[1:], previous_hash=events[0].event_hash, previous_sequence=1
        )
        is None
    )
    from opsmesh.governance.audit.models import AuditChainHead

    with database_sessions() as session:
        head = session.get(AuditChainHead, audit.CHAIN_HEAD_ID)
        assert head is not None
        assert head.last_sequence == 2
        assert head.last_hash == events[1].event_hash
