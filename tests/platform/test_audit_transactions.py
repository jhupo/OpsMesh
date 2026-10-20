"""PG transactions prove one event fact, authorization and atomic Outbox intent."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.platform.audit.models import AuditEvent
from opsmesh.shared.approvals import ApprovalTarget, approvals_for
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from tests.platform.test_audit_approvals_postgres import Subject


def occurrence():
    return AuditRecord(
        category="security",
        action="sample.security",
        source="platform",
        actor=AuditActor("user", str(uuid4())),
        summary="sensitive occurrence",
        payload_json='{"secret":"hidden-value","outcome":"denied"}',
    )


def test_occurrence_replay_fingerprint_and_outbox_commit_together(database_sessions):
    event = occurrence()
    with database_sessions() as session, session.begin():
        record_audit(session, event)
        record_audit(session, event)
        with pytest.raises(ConflictError):
            # A conflicting occurrence ID is rejected by the recording owner.
            from opsmesh.platform.audit.service import append_audit_record
            from opsmesh.platform.policies.redaction import PLATFORM_REDACTION

            append_audit_record(
                session, replace(event, summary="changed"), rules=PLATFORM_REDACTION
            )
    with database_sessions() as session:
        fact = session.get(AuditEvent, event.event_id)
        assert fact.details["secret"] != "hidden-value"
        assert fact.category == "security"
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 1


def test_audit_write_failure_rolls_back_fact_and_outbox(database_sessions):
    from opsmesh.platform.audit.service import append_audit_record
    from opsmesh.platform.policies.redaction import PLATFORM_REDACTION

    def broken(session, record):
        append_audit_record(session, record, rules=PLATFORM_REDACTION)
        session.flush()
        raise RuntimeError("audit storage failed")

    with (
        pytest.raises(RuntimeError, match="audit storage"),
        database_sessions() as session,
        session.begin(),
    ):
        session.info["audit_writer"] = broken
        record_audit(session, occurrence())
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 0


def test_unrelated_audit_append_does_not_wait_for_an_uncommitted_writer(database_sessions):
    import threading

    from sqlalchemy import text

    from opsmesh.platform.audit.service import append_audit_record
    from opsmesh.platform.policies.redaction import PLATFORM_REDACTION

    first, second = occurrence(), occurrence()
    errors = []

    def append_other():
        try:
            with database_sessions() as session, session.begin():
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
                append_audit_record(session, second, rules=PLATFORM_REDACTION)
                session.flush()
        except Exception as error:
            errors.append(error)

    with database_sessions() as session, session.begin():
        append_audit_record(session, first, rules=PLATFORM_REDACTION)
        session.flush()
        worker = threading.Thread(target=append_other)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert errors == []
    with database_sessions() as session:
        assert session.get(AuditEvent, first.event_id) is not None
        assert session.get(AuditEvent, second.event_id) is not None


def test_approval_role_revocation_and_new_attempt_after_rejection(database_sessions):
    requester = Subject(uuid4())
    admin = Subject(uuid4(), admin=True)
    target = ApprovalTarget("generic", str(uuid4()), "revision-1")
    with database_sessions() as session, session.begin():
        port = approvals_for(session)
        request = port.create(
            session,
            kind="test",
            target=target,
            title="Publish",
            summary="",
            details={},
            approver_role="admin",
            requester=requester,
            request_key="one",
        )
        # Use target-independent notifier to verify role authorization without an execution target.
        rejected = port.decide(
            session,
            approval_id=request.id,
            decision="rejected",
            decided_by=admin,
            reason="change needed",
            idempotency_key="decision-1",
        )
        assert rejected.status == "rejected"
        revoked = Subject(admin.user_id)
        with pytest.raises(ForbiddenError):
            port.decide(
                session,
                approval_id=request.id,
                decision="rejected",
                decided_by=revoked,
                reason="change needed",
                idempotency_key="decision-1",
            )
        next_attempt = port.create(
            session,
            kind="test",
            target=target,
            title="Publish",
            summary="",
            details={},
            approver_role="admin",
            requester=requester,
            request_key="two",
        )
        assert next_attempt.id != request.id and next_attempt.status == "pending"
        with pytest.raises(ForbiddenError):
            port.cancel(
                session, approval_id=next_attempt.id, actor=Subject(uuid4()), reason="other"
            )
