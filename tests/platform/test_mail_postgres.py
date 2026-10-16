"""S4 platform mail tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for mail: source admission, send
idempotency, the claim lease and the unknown-outcome classification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor
from opsmesh.platform.mail import service as mail
from opsmesh.platform.mail.models import (
    MAIL_CANCELLED,
    MAIL_FAILED,
    MAIL_NEEDS_ATTENTION,
    MAIL_PENDING,
    MAIL_SENDING,
    MAIL_SENT,
    MailIntent,
)
from opsmesh.shared.errors import (
    ConflictError,
    ForbiddenError,
    InvalidArgumentError,
    NotFoundError,
)
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry

TEST_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="


def cipher() -> SecretCipher:
    return SecretCipher(key=TEST_KEY, key_id="test")


def platform_actor() -> AuditActor:
    return AuditActor(actor_type="platform")


def register_test_mail(database_sessions, *, idempotency_key: str = "mail-key-0001"):
    with database_sessions() as session, session.begin():
        intent, replayed = mail.register_mail(
            session,
            source=mail.PLATFORM_TEST_SOURCE,
            recipient="user@example.com",
            subject=mail.PLATFORM_TEST_SUBJECT,
            body=mail.PLATFORM_TEST_BODY,
            idempotency_key=idempotency_key,
            authorization=mail.PlatformTestAuthorization(),
            cipher=cipher(),
        )
        return intent.id, replayed


def test_a_registered_send_is_staged_as_one_durable_execution(database_sessions) -> None:
    intent_id, replayed = register_test_mail(database_sessions)
    assert replayed is False
    with database_sessions() as session:
        intent = session.get(MailIntent, intent_id)
        assert intent is not None
        assert intent.status == MAIL_PENDING
        assert intent.attempts == 0
        assert intent.outcome_unknown is False
        staged = list(session.scalars(select(TemporalOutboxEntry)))
    assert len(staged) == 1
    assert staged[0].workflow_id == f"mail-send:{intent_id}"


def test_the_same_key_and_content_replays_the_registration(database_sessions) -> None:
    intent_id, replayed = register_test_mail(database_sessions)
    assert replayed is False
    with database_sessions() as session, session.begin():
        again, replayed_again = mail.register_mail(
            session,
            source=mail.PLATFORM_TEST_SOURCE,
            recipient="user@example.com",
            subject=mail.PLATFORM_TEST_SUBJECT,
            body=mail.PLATFORM_TEST_BODY,
            idempotency_key="mail-key-0001",
            authorization=mail.PlatformTestAuthorization(),
            cipher=cipher(),
        )
        assert replayed_again is True
        assert again.id == intent_id
    with database_sessions() as session:
        rows = list(session.scalars(select(MailIntent)))
    assert len(rows) == 1


def test_the_same_key_with_other_content_conflicts(database_sessions) -> None:
    register_test_mail(database_sessions)
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        mail.register_mail(
            session,
            source=mail.PLATFORM_TEST_SOURCE,
            recipient="user@example.com",
            subject="Another subject",
            body=mail.PLATFORM_TEST_BODY,
            idempotency_key="mail-key-0001",
            authorization=mail.PlatformTestAuthorization(),
            cipher=cipher(),
        )


def test_an_unadmitted_source_is_refused(database_sessions) -> None:
    class Rejecting:
        def authorize(self, session: Session, scope: mail.SourceScope) -> None:
            raise ForbiddenError("The mail source is not registered")

    with database_sessions() as session, pytest.raises(ForbiddenError), session.begin():
        mail.register_mail(
            session,
            source="extension:unknown",
            recipient="user@example.com",
            subject="Hello",
            body="Body",
            idempotency_key="mail-key-0002",
            authorization=Rejecting(),
            cipher=cipher(),
        )


def test_the_daily_source_limit_bounds_one_source(database_sessions) -> None:
    with database_sessions() as session, session.begin():
        mail.register_mail(
            session,
            source=mail.PLATFORM_TEST_SOURCE,
            recipient="user@example.com",
            subject=mail.PLATFORM_TEST_SUBJECT,
            body=mail.PLATFORM_TEST_BODY,
            idempotency_key="mail-key-0001",
            authorization=mail.PlatformTestAuthorization(),
            cipher=cipher(),
            daily_source_limit=1,
        )
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        mail.register_mail(
            session,
            source=mail.PLATFORM_TEST_SOURCE,
            recipient="other@example.com",
            subject=mail.PLATFORM_TEST_SUBJECT,
            body=mail.PLATFORM_TEST_BODY,
            idempotency_key="mail-key-0002",
            authorization=mail.PlatformTestAuthorization(),
            cipher=cipher(),
            daily_source_limit=1,
        )


def test_a_claimed_intent_cannot_be_claimed_again_before_its_lease(database_sessions) -> None:
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        claimed = mail.mark_sending(session, intent_id=intent_id)
        assert claimed.status == MAIL_SENDING
        assert claimed.attempts == 1
        with pytest.raises(ConflictError):
            mail.mark_sending(session, intent_id=intent_id)
    with database_sessions() as session, session.begin():
        intent = session.get(MailIntent, intent_id)
        assert intent is not None
        intent.claimed_at = datetime.now(UTC) - timedelta(seconds=mail.MAIL_CLAIM_LEASE_SECONDS + 1)
    with database_sessions() as session, session.begin():
        reclaimed = mail.mark_sending(session, intent_id=intent_id)
        assert reclaimed.attempts == 2


def test_an_unconfirmed_outcome_is_reconciled_never_resent(database_sessions) -> None:
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        mail.mark_sending(session, intent_id=intent_id)
        mail.mark_needs_attention(session, intent_id=intent_id, reason="smtp timeout")
        intent = session.get(MailIntent, intent_id)
        assert intent is not None
        assert intent.status == MAIL_NEEDS_ATTENTION
        assert intent.outcome_unknown is True
    with database_sessions() as session, session.begin():
        reconciled = mail.reconcile_intent(
            session,
            intent_id=intent_id,
            outcome="sent",
            actor=platform_actor(),
            note="confirmed with the provider",
        )
        assert reconciled.status == MAIL_SENT
        assert reconciled.outcome_unknown is False
        assert reconciled.reconciliation_note == "confirmed with the provider"
        with pytest.raises(ConflictError):
            mail.reconcile_intent(
                session,
                intent_id=intent_id,
                outcome="failed",
                actor=platform_actor(),
            )


def test_a_pending_intent_is_cancelled_and_a_sent_one_is_not(database_sessions) -> None:
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        cancelled = mail.cancel_intent(session, intent_id=intent_id, actor=platform_actor())
        assert cancelled.status == MAIL_CANCELLED
        with pytest.raises(ConflictError):
            mail.cancel_intent(session, intent_id=intent_id, actor=platform_actor())
    second_id, _replayed = register_test_mail(database_sessions, idempotency_key="mail-key-0003")
    with database_sessions() as session, session.begin():
        mail.mark_sending(session, intent_id=second_id)
        mail.mark_sent(session, intent_id=second_id)
        with pytest.raises(ConflictError):
            mail.cancel_intent(session, intent_id=second_id, actor=platform_actor())


def test_a_workflow_cancellation_during_a_send_needs_attention(database_sessions) -> None:
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        mail.mark_sending(session, intent_id=intent_id)
        cancelled = mail.mark_cancelled(session, intent_id=intent_id)
        assert cancelled.status == MAIL_NEEDS_ATTENTION
        assert cancelled.outcome_unknown is True
        assert cancelled.failure_reason == "cancelled_during_send"


def test_a_pending_workflow_cancellation_is_a_plain_cancellation(database_sessions) -> None:
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        cancelled = mail.mark_cancelled(session, intent_id=intent_id)
        assert cancelled.status == MAIL_CANCELLED


def test_a_known_refusal_is_recorded_as_failed(database_sessions) -> None:
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        mail.mark_sending(session, intent_id=intent_id)
        failed = mail.mark_failed(session, intent_id=intent_id, reason="mailbox unavailable")
        assert failed.status == MAIL_FAILED
        assert failed.outcome_unknown is False


def test_the_smtp_password_is_stored_encrypted_and_read_back(database_sessions) -> None:
    with database_sessions() as session, session.begin():
        configuration = mail.configure_smtp(
            session,
            values=mail.SmtpConfigurationInput(
                host="smtp.example.com",
                port=587,
                security="starttls",
                username="mailer",
                password="smtp-secret",
                from_email="ops@example.com",
                from_name="OpsMesh",
            ),
            actor=platform_actor(),
            cipher=cipher(),
        )
        assert configuration.password_ciphertext is not None
        assert "smtp-secret" not in str(configuration.password_ciphertext)
        settings = mail.smtp_settings_of(configuration, cipher())
        assert settings.password == "smtp-secret"
        assert settings.host == "smtp.example.com"


def test_an_unknown_smtp_security_mode_is_refused(database_sessions) -> None:
    with database_sessions() as session, pytest.raises(InvalidArgumentError), session.begin():
        mail.configure_smtp(
            session,
            values=mail.SmtpConfigurationInput(
                host="smtp.example.com",
                port=587,
                security="carrier-pigeon",
                username="mailer",
                password="smtp-secret",
                from_email="ops@example.com",
                from_name="OpsMesh",
            ),
            actor=platform_actor(),
            cipher=cipher(),
        )


def test_the_intent_page_reports_its_total(database_sessions) -> None:
    register_test_mail(database_sessions, idempotency_key="mail-key-0001")
    register_test_mail(database_sessions, idempotency_key="mail-key-0002")
    with database_sessions() as session:
        rows, total = mail.list_intents(session, page=PageParams(limit=1, offset=0))
    assert total == 2
    assert len(rows) == 1


def test_an_unknown_intent_is_not_found(database_sessions) -> None:
    with database_sessions() as session, pytest.raises(NotFoundError):
        mail.get_intent(session, uuid4())
