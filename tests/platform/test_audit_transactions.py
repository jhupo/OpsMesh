"""PostgreSQL audit occurrence replay, redaction and caller-owned rollback."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.platform.audit.service import append_audit_record
from opsmesh.shared.errors import ConflictError
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry


def occurrence():
    return AuditRecord(
        category="security",
        action="sample.security",
        source="platform",
        actor=AuditActor("user", str(uuid4())),
        summary="Sensitive occurrence",
        payload_json='{"secret":"hidden-value","outcome":"denied"}',
    )


def test_occurrence_replay_is_one_redacted_fact_without_background_dispatch(database_sessions):
    event = occurrence()
    with database_sessions() as session, session.begin():
        record_audit(session, event)
        record_audit(session, event)
        with pytest.raises(ConflictError):
            append_audit_record(
                session, replace(event, summary="changed"), rules=PLATFORM_REDACTION
            )
    with database_sessions() as session:
        assert session.get(AuditEvent, event.event_id).details["secret"] == "[redacted]"
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 0


def test_audit_write_failure_rolls_back_the_callers_transaction(database_sessions):
    event = occurrence()
    with (
        pytest.raises(RuntimeError, match="audit storage"),
        database_sessions() as session,
        session.begin(),
    ):
        record_audit(session, event)
        session.flush()
        raise RuntimeError("audit storage failed")
    with database_sessions() as session:
        assert session.get(AuditEvent, event.event_id) is None


def test_unrelated_audit_append_does_not_wait_for_an_uncommitted_writer(database_sessions):
    import threading

    from sqlalchemy import text

    first, second = occurrence(), occurrence()
    errors = []

    def append_other():
        try:
            with database_sessions() as session, session.begin():
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
                record_audit(session, second)
                session.flush()
        except Exception as error:
            errors.append(error)

    with database_sessions() as session, session.begin():
        record_audit(session, first)
        session.flush()
        worker = threading.Thread(target=append_other)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert errors == []
    with database_sessions() as session:
        assert session.get(AuditEvent, first.event_id) is not None
        assert session.get(AuditEvent, second.event_id) is not None
