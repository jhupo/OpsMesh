"""S4 governance tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for governance: independent audit facts,
idempotent extension appends and the single-decision approval contract.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.platform.approvals import service as approvals
from opsmesh.platform.audit import service as audit
from opsmesh.platform.audit.models import AuditAppendReceipt, AuditEvent
from opsmesh.shared.approvals import ApprovalTarget
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError

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


def load_events(database_sessions) -> list[AuditEvent]:
    with database_sessions() as session:
        return list(session.scalars(select(AuditEvent).order_by(AuditEvent.sequence.asc())))


def test_audit_events_have_distinct_database_assigned_sequences(database_sessions) -> None:
    append_two_events(database_sessions)
    events = load_events(database_sessions)
    assert len(events) == 2
    assert events[0].sequence < events[1].sequence
    assert events[0].action == "user.created"
    assert events[1].details == {"changed": ["display_name"]}


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


GOVERNANCE_CONCURRENCY_TABLES = (
    "temporal_dispatches",
    "http_creation_receipts",
    "audit_append_receipts",
    "audit_events",
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
    """The receipt is decided under its own idempotency lock.

    Two concurrent appends of one idempotency key both find no receipt before
    either lock; the loser must read the winner's committed receipt instead of
    appending a second event that only the unique constraint would stop.
    """
    from tests.conftest import session_dependency_info

    sessions = sessionmaker(
        bind=database_engine,
        autoflush=False,
        expire_on_commit=False,
        info=session_dependency_info(),
    )
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
            session.scalars(select(AuditEvent).where(AuditEvent.target_id == race_target))
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
            target=ApprovalTarget(target_type="capability_version", target_id="target-1"),
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
            target=ApprovalTarget(target_type="capability_version", target_id="target-1"),
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
            target=ApprovalTarget(target_type="capability_version", target_id="target-1"),
            title="Publish expert",
            summary="Publish one expert version",
            details={"version": "1.0.0"},
            approver_user_id=USER_ID,
        )
        replay = approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(target_type="capability_version", target_id="target-1"),
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
                target=ApprovalTarget(target_type="capability_version", target_id="target-1"),
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
            target=ApprovalTarget(target_type="capability_version", target_id="target-2"),
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
            target=ApprovalTarget(target_type="capability_version", target_id="target-1"),
            title="Publish expert",
            summary="Publish one expert version",
            details={},
            approver_user_id=USER_ID,
        )
        approvals.create_request(
            session,
            kind="capability.publish",
            target=ApprovalTarget(target_type="capability_version", target_id="target-2"),
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
