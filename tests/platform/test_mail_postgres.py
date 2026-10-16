"""S4 platform mail tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for mail: source admission, send
idempotency, the claim lease and the unknown-outcome classification.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.bootstrap.mail import (
    InvitationMailAuthorization,
    InvitationMailRegistrar,
    mail_source_authorizations,
)
from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.invitations import service as invitations
from opsmesh.platform.mail import service as mail
from opsmesh.platform.mail.activities import MailActivities
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
        claimed = mail.claim_intent(session, intent_id=intent_id)
        assert claimed.status == MAIL_SENDING
        assert claimed.attempts == 1
        with pytest.raises(ConflictError):
            mail.claim_intent(session, intent_id=intent_id)


def test_an_expired_claim_is_withdrawn_never_resent(database_sessions) -> None:
    """A crashed attempt's outcome is unknown: it is reconciled, not resent."""
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        mail.claim_intent(session, intent_id=intent_id)
    with database_sessions() as session, session.begin():
        intent = session.get(MailIntent, intent_id)
        assert intent is not None
        intent.claimed_at = datetime.now(UTC) - timedelta(seconds=mail.MAIL_CLAIM_LEASE_SECONDS + 1)
    with database_sessions() as session, session.begin():
        withdrawn = mail.claim_intent(session, intent_id=intent_id)
        assert withdrawn.status == MAIL_NEEDS_ATTENTION
        assert withdrawn.outcome_unknown is True
        assert withdrawn.failure_reason == "attempt_uncertain"
        # No new attempt is recorded: the uncertain one stays the only one.
        assert withdrawn.attempts == 1
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


def test_an_unconfirmed_outcome_is_reconciled_never_resent(database_sessions) -> None:
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        mail.claim_intent(session, intent_id=intent_id)
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
        mail.claim_intent(session, intent_id=second_id)
        mail.mark_sent(session, intent_id=second_id)
        with pytest.raises(ConflictError):
            mail.cancel_intent(session, intent_id=second_id, actor=platform_actor())


def test_an_in_flight_intent_is_never_cancelled_over_a_live_send(database_sessions) -> None:
    """A cancel must not report success while the worker is already sending."""
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        mail.claim_intent(session, intent_id=intent_id)
        with pytest.raises(ConflictError) as refusal:
            mail.cancel_intent(session, intent_id=intent_id, actor=platform_actor())
        assert refusal.value.code == "mail_in_flight"
        intent = session.get(MailIntent, intent_id)
        assert intent is not None
        assert intent.status == MAIL_SENDING


def test_a_workflow_cancellation_during_a_send_needs_attention(database_sessions) -> None:
    intent_id, _replayed = register_test_mail(database_sessions)
    with database_sessions() as session, session.begin():
        mail.claim_intent(session, intent_id=intent_id)
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
        mail.claim_intent(session, intent_id=intent_id)
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


def _configure_refusing_smtp(database_sessions) -> None:
    with database_sessions() as session, session.begin():
        mail.configure_smtp(
            session,
            values=mail.SmtpConfigurationInput(
                host="127.0.0.1",
                port=1,
                security="starttls",
                username="",
                password="unused",
                from_email="ops@example.com",
                from_name="OpsMesh",
                timeout_seconds=10,
            ),
            actor=platform_actor(),
            cipher=cipher(),
        )


def test_a_delivery_without_smtp_configuration_ends_failed_not_sending(
    database_sessions, async_database_sessions
) -> None:
    """A deterministic pre-send failure must leave a durable terminal state."""
    intent_id, _replayed = register_test_mail(database_sessions)
    activities = MailActivities(cipher(), async_database_sessions, mail_source_authorizations())
    outcome = asyncio.run(activities.deliver_mail_intent(intent_id))
    assert outcome.delivered is False
    assert outcome.reason == "smtp_not_configured"
    with database_sessions() as session:
        intent = session.get(MailIntent, intent_id)
        assert intent is not None
        assert intent.status == MAIL_FAILED
        assert intent.outcome_unknown is False


def test_a_delivery_re_admits_its_source_and_refuses_a_revoked_invitation(
    database_sessions, async_database_sessions
) -> None:
    """Registration-time admission cannot outlive a revoked invitation."""
    registrar = InvitationMailRegistrar(
        authorization=InvitationMailAuthorization(),
        cipher=cipher(),
        daily_source_limit=100,
    )
    with database_sessions() as session, session.begin():
        invitation, _token = invitations.create_invitation(
            session,
            email="invited@example.com",
            invited_by=platform_actor(),
            sender=registrar,
        )
    with database_sessions() as session, session.begin():
        invitations.revoke_invitation(
            session, invitation_id=invitation.id, actor=platform_actor()
        )
    with database_sessions() as session:
        intent = session.scalar(
            select(MailIntent).where(MailIntent.source_ref == str(invitation.id))
        )
        assert intent is not None
        intent_id = intent.id
    activities = MailActivities(cipher(), async_database_sessions, mail_source_authorizations())
    outcome = asyncio.run(activities.deliver_mail_intent(intent_id))
    assert outcome.delivered is False
    assert outcome.reason == "source_not_admitted:conflict"
    with database_sessions() as session:
        intent = session.get(MailIntent, intent_id)
        assert intent is not None
        assert intent.status == MAIL_FAILED
        assert intent.outcome_unknown is False


def test_a_slow_send_does_not_block_the_event_loop(
    database_sessions, async_database_sessions, monkeypatch
) -> None:
    """The synchronous SMTP client must run off the worker's event loop."""
    intent_id, _replayed = register_test_mail(database_sessions)
    _configure_refusing_smtp(database_sessions)
    started = threading.Event()
    release = threading.Event()

    def slow_send(settings: object, *, recipient: str, subject: str, body: str) -> None:
        started.set()
        release.wait(timeout=15)

    monkeypatch.setattr("opsmesh.platform.mail.activities.send_mail", slow_send)
    activities = MailActivities(cipher(), async_database_sessions, mail_source_authorizations())

    async def run() -> object:
        task = asyncio.create_task(activities.deliver_mail_intent(intent_id))
        while not started.is_set():
            await asyncio.sleep(0.01)
        # A timer must be able to elapse while the send is in flight: a send
        # running on the loop itself would block this wait until it finished.
        await asyncio.sleep(0.1)
        release.set()
        return await asyncio.wait_for(task, timeout=30)

    outcome = asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert outcome.delivered is True


def test_a_refused_smtp_endpoint_ends_the_intent_failed(
    database_sessions, async_database_sessions
) -> None:
    """A known transport refusal is a durable failure, never a resend."""
    intent_id, _replayed = register_test_mail(database_sessions)
    _configure_refusing_smtp(database_sessions)
    activities = MailActivities(cipher(), async_database_sessions, mail_source_authorizations())
    outcome = asyncio.run(activities.deliver_mail_intent(intent_id))
    assert outcome.delivered is False
    assert outcome.reason == "ConnectionRefusedError"
    with database_sessions() as session:
        intent = session.get(MailIntent, intent_id)
        assert intent is not None
        assert intent.status == MAIL_FAILED
        assert intent.outcome_unknown is False


def test_a_repeat_smtp_test_replays_and_a_new_configuration_starts_a_new_one(
    database_sessions,
) -> None:
    """One test operation is bounded by the configuration revision it tested."""
    from opsmesh.identity.context import CurrentUser
    from opsmesh.platform.mail.routes import test_smtp
    from opsmesh.platform.mail.schemas import TestMailRequest

    admin = CurrentUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="Administrator",
        status="active",
        platform_roles=frozenset({"admin", "member"}),
    )
    with database_sessions() as session:
        first = test_smtp(TestMailRequest(recipient="ops@example.com"), session, admin, cipher())
    with database_sessions() as session:
        replay = test_smtp(TestMailRequest(recipient="ops@example.com"), session, admin, cipher())
    assert replay.intent_id == first.intent_id
    with database_sessions() as session, session.begin():
        mail.configure_smtp(
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
    with database_sessions() as session:
        after_reconfiguration = test_smtp(
            TestMailRequest(recipient="ops@example.com"), session, admin, cipher()
        )
    assert after_reconfiguration.intent_id != first.intent_id


def concurrent_mail_sessions(engine) -> sessionmaker[Session]:
    """One sessionmaker per worker thread over the shared test engine."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        info={
            "dispatch_cipher": cipher(),
            "dispatch_trace_context": lambda: {},
        },
    )


def reset_mail_tables(engine) -> None:
    """Start one concurrency test from a known-empty mail state."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE temporal_dispatches, http_creation_receipts, mail_intents, "
                "smtp_configurations, audit_events, audit_chain_head, "
                "audit_append_receipts, audit_verifications"
            )
        )


@pytest.fixture
def clean_concurrency_tables(database_engine):
    """Empty the database around a concurrency test, before and after it."""
    reset_mail_tables(database_engine)
    yield
    reset_mail_tables(database_engine)


def test_concurrent_registrations_of_one_key_replay_one_intent(
    database_engine, clean_concurrency_tables
) -> None:
    """The source-day boundary makes the same-key replay exact under races."""
    sessions = concurrent_mail_sessions(database_engine)
    race_key = f"mail-race-{uuid4()}"
    start = threading.Event()
    intents: dict[str, object] = {}
    replays: dict[str, bool] = {}
    errors: dict[str, str] = {}

    def register(name: str) -> None:
        start.wait(timeout=10)
        try:
            with sessions() as session, session.begin():
                intent, replayed = mail.register_mail(
                    session,
                    source=mail.PLATFORM_TEST_SOURCE,
                    recipient="racer@example.com",
                    subject=mail.PLATFORM_TEST_SUBJECT,
                    body=mail.PLATFORM_TEST_BODY,
                    idempotency_key=race_key,
                    authorization=mail.PlatformTestAuthorization(),
                    cipher=cipher(),
                )
            intents[name] = intent.id
            replays[name] = replayed
        except Exception as exc:  # noqa: BLE001 - the race must not surface one
            errors[name] = type(exc).__name__

    threads = [
        threading.Thread(target=register, args=("first",)),
        threading.Thread(target=register, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == {}
    assert intents["first"] == intents["second"]
    assert sorted(replays.values()) == [False, True]
    with sessions() as session:
        rows = list(
            session.scalars(
                select(MailIntent).where(MailIntent.idempotency_key == race_key)
            )
        )
        staged = list(
            session.scalars(
                select(TemporalOutboxEntry).where(
                    TemporalOutboxEntry.workflow_id == f"mail-send:{intents['first']}"
                )
            )
        )
    assert len(rows) == 1
    assert len(staged) == 1


def test_the_daily_limit_admits_exactly_one_concurrent_registration(
    database_engine, clean_concurrency_tables
) -> None:
    """The admission count runs inside the source-day lock, never beside it."""
    sessions = concurrent_mail_sessions(database_engine)
    start = threading.Event()
    outcomes: dict[str, str] = {}

    def register(name: str) -> None:
        start.wait(timeout=10)
        try:
            with sessions() as session, session.begin():
                mail.register_mail(
                    session,
                    source=mail.PLATFORM_TEST_SOURCE,
                    recipient=f"{name}@example.com",
                    subject=mail.PLATFORM_TEST_SUBJECT,
                    body=mail.PLATFORM_TEST_BODY,
                    idempotency_key=f"mail-limit-{name}-{uuid4()}",
                    authorization=mail.PlatformTestAuthorization(),
                    cipher=cipher(),
                    daily_source_limit=1,
                )
            outcomes[name] = "registered"
        except ConflictError:
            outcomes[name] = "refused"

    threads = [
        threading.Thread(target=register, args=("first",)),
        threading.Thread(target=register, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes.values()) == ["refused", "registered"]
    with sessions() as session:
        rows = list(
            session.scalars(
                select(MailIntent).where(MailIntent.source == mail.PLATFORM_TEST_SOURCE)
            )
        )
    assert len(rows) == 1
