"""S4 identity tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for identity: immediate session expiry
and revocation, the quota ledger, invitation token rotation and account
opening.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.governance.audit.service import AuditActor
from opsmesh.governance.security_events.service import SecurityEventLog
from opsmesh.identity.access import PlatformRole
from opsmesh.identity.auth.models import LoginSession
from opsmesh.identity.auth.service import AuthService, token_digest
from opsmesh.identity.errors import (
    AuthenticationError,
    InvalidCredentialsError,
    InvitationStateError,
    QuotaExceededError,
    UserDisabledError,
)
from opsmesh.identity.invitations import service as invitations
from opsmesh.identity.invitations.contracts import OpenedInvitationAccount
from opsmesh.identity.provisioning import (
    AccountDefaults,
    ProvisionedAccountOpening,
    UserIdentityDirectory,
)
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.quotas.models import TokenReservation, UserTokenAccount
from opsmesh.identity.users import service as users
from opsmesh.identity.users.models import UserProfile
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.secrets import SecretCipher

ADMIN_EMAIL = "admin@example.com"
MEMBER_EMAIL = "member@example.com"
MEMBER_ROLES = frozenset({str(PlatformRole.MEMBER)})
ADMIN_ROLES = frozenset({str(PlatformRole.ADMIN), str(PlatformRole.MEMBER)})


def auth_service() -> AuthService:
    return AuthService(
        account_opening=ProvisionedAccountOpening(AccountDefaults(token_limit=1_000)),
        user_directory=UserIdentityDirectory(),
        security_events=SecurityEventLog(),
    )


def open_account(
    sessions: sessionmaker[Session],
    *,
    email: str,
    display_name: str,
    password: str,
    roles: frozenset[str] = MEMBER_ROLES,
) -> tuple[UUID, UUID]:
    """Register one account; returns the user id and its session token."""
    service = auth_service()
    with sessions() as session, session.begin():
        result = service.register(
            session, email=email, display_name=display_name, password=password
        )
    if roles != MEMBER_ROLES:
        with sessions() as session, session.begin():
            users.set_platform_roles(
                session,
                user_id=result.user.user_id,
                roles=roles,
                actor=AuditActor(actor_type="platform"),
            )
    return result.user.user_id, result.token


def test_registration_login_and_authentication(database_sessions) -> None:
    service = auth_service()
    with database_sessions() as session:
        with session.begin():
            result = service.register(
                session, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
            )
            token = result.token
        identity = service.authenticate(session, token=token)
        assert identity.email == MEMBER_EMAIL
        assert identity.has_platform_role("member")


def test_the_stored_session_never_equals_the_public_id_or_the_token(database_sessions) -> None:
    """A read-only database leak must not yield a usable session token."""
    service = auth_service()
    user_id, token = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    with database_sessions() as session:
        rows = list(session.scalars(select(LoginSession)))
        assert len(rows) == 1
        stored = rows[0]
        assert stored.id != token
        assert stored.token_hash != str(token)
        assert stored.token_hash == token_digest(token)
        listed = service.list_sessions(session, user_id=user_id)
        assert [row[0] for row in listed] == [stored.id]
        # The public id alone cannot authenticate; only the token can.
        with pytest.raises(AuthenticationError):
            service.authenticate(session, token=stored.id)
        assert service.authenticate(session, token=token).user_id == user_id


def test_a_wrong_password_records_its_security_fact(database_sessions) -> None:
    service = auth_service()
    with database_sessions() as session, session.begin():
        service.register(
            session, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
        )
    with database_sessions() as session:
        with pytest.raises(InvalidCredentialsError), session.begin():
            service.login(session, email=MEMBER_EMAIL, password="wrong-password")
        # The failed attempt rolled back; its fact is committed on its own.
        with session.begin():
            service.record_login_failure(session, email=MEMBER_EMAIL)
    from opsmesh.governance.security_events.models import SecurityEvent

    with database_sessions() as session:
        facts = list(
            session.scalars(select(SecurityEvent).where(SecurityEvent.user_id.is_not(None)))
        )
    assert [fact.kind for fact in facts] == ["login.invalid_credentials"]


def test_an_expired_session_is_rejected_immediately(database_sessions) -> None:
    service = auth_service()
    user_id, token = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    with database_sessions() as session, session.begin():
        listed = service.list_sessions(session, user_id=user_id)
        assert len(listed) == 1
        public_id = listed[0][0]
        login_session = session.get(LoginSession, public_id)
        assert login_session is not None
        login_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with database_sessions() as session, pytest.raises(AuthenticationError):
        service.authenticate(session, token=token)


def test_a_revoked_session_is_rejected_immediately(database_sessions) -> None:
    service = auth_service()
    _user_id_, session_id = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    with database_sessions() as session, session.begin():
        service.logout(session, token=session_id)
    with database_sessions() as session, pytest.raises(AuthenticationError):
        service.authenticate(session, token=session_id)


def test_a_password_change_revokes_every_open_session(database_sessions) -> None:
    service = auth_service()
    user_id, session_id = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    with database_sessions() as session, session.begin():
        service.change_password(
            session,
            user_id=user_id,
            current_password="correct-horse",
            new_password="rotated-horse-1",
        )
    with database_sessions() as session, pytest.raises(AuthenticationError):
        service.authenticate(session, token=session_id)
    with database_sessions() as session, session.begin():
        result = service.login(session, email=MEMBER_EMAIL, password="rotated-horse-1")
    assert result.user.user_id == user_id


def test_a_disabled_account_cannot_authenticate(database_sessions) -> None:
    service = auth_service()
    user_id, _session_id = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    with database_sessions() as session, session.begin():
        users.set_status(
            session,
            user_id=user_id,
            status="disabled",
            actor=AuditActor(actor_type="platform"),
        )
    with database_sessions() as session, pytest.raises(UserDisabledError), session.begin():
        service.login(session, email=MEMBER_EMAIL, password="correct-horse")


def test_the_last_administrator_is_protected(database_sessions) -> None:
    user_id, _session_id = open_account(
        database_sessions,
        email=ADMIN_EMAIL,
        display_name="Administrator",
        password="correct-horse",
        roles=ADMIN_ROLES,
    )
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        users.set_platform_roles(
            session,
            user_id=user_id,
            roles=frozenset({str(PlatformRole.MEMBER)}),
            actor=AuditActor(actor_type="platform"),
        )


def test_the_token_ledger_serializes_reservations(database_sessions) -> None:
    user_id, _session_id = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    with database_sessions() as session, session.begin():
        first = quotas.reserve_tokens(
            session,
            user_id=user_id,
            amount=400,
            idempotency_scope="run:1",
            idempotency_key="reserve-1",
        )
        replay = quotas.reserve_tokens(
            session,
            user_id=user_id,
            amount=400,
            idempotency_scope="run:1",
            idempotency_key="reserve-1",
        )
        assert replay.id == first.id
        snapshot = quotas.get_quota(session, user_id)
        assert snapshot.reserved_tokens == 400
        assert snapshot.available_tokens == 600
        with pytest.raises(QuotaExceededError):
            quotas.reserve_tokens(
                session,
                user_id=user_id,
                amount=700,
                idempotency_scope="run:1",
                idempotency_key="reserve-1b",
            )
    with database_sessions() as session, session.begin():
        quotas.settle_tokens(session, reservation_id=first.id, actual_amount=250)
        snapshot = quotas.get_quota(session, user_id)
        assert snapshot.used_tokens == 250
        assert snapshot.reserved_tokens == 0
        assert snapshot.available_tokens == 750


def test_a_reused_reservation_key_with_another_amount_conflicts(database_sessions) -> None:
    user_id, _session_id = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    with database_sessions() as session, session.begin():
        quotas.reserve_tokens(
            session,
            user_id=user_id,
            amount=100,
            idempotency_scope="run:1",
            idempotency_key="reserve-1",
        )
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        quotas.reserve_tokens(
            session,
            user_id=user_id,
            amount=200,
            idempotency_scope="run:1",
            idempotency_key="reserve-1",
        )


def test_a_reservation_beyond_the_limit_is_refused(database_sessions) -> None:
    user_id, _session_id = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    with database_sessions() as session, session.begin():
        quotas.reserve_tokens(
            session,
            user_id=user_id,
            amount=900,
            idempotency_scope="run:1",
            idempotency_key="reserve-1",
        )
    with database_sessions() as session, pytest.raises(QuotaExceededError), session.begin():
        quotas.reserve_tokens(
            session,
            user_id=user_id,
            amount=200,
            idempotency_scope="run:1",
            idempotency_key="reserve-2",
        )


class RecordingSender:
    """The invitation mail registrar contract, recorded instead of delivered."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_account_invitation(
        self,
        session: Session,
        *,
        invitation_id: UUID,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> None:
        self.calls.append(
            {
                "invitation_id": invitation_id,
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "idempotency_key": idempotency_key,
            }
        )


class RecordingOpening:
    """The account opening contract, recording instead of writing users."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def open_account(
        self, session: Session, *, email: str, display_name: str, password: str
    ) -> OpenedInvitationAccount:
        self.calls.append({"email": email, "display_name": display_name})
        return OpenedInvitationAccount(
            user_id=uuid4(), session_token=uuid4(), expires_at=datetime.now(UTC)
        )


def test_an_invitation_rotates_its_token_on_resend(database_sessions) -> None:
    admin = AuditActor(actor_type="platform")
    sender = RecordingSender()
    with database_sessions() as session, session.begin():
        invitation, token = invitations.create_invitation(
            session,
            email="invitee@example.com",
            invited_by=admin,
            sender=sender,
            platform_base_url="https://opsmesh.example.com",
        )
        first_digest = invitation.token_hash
        resent = invitations.resend_invitation(
            session,
            invitation_id=invitation.id,
            invited_by=admin,
            sender=sender,
            platform_base_url="https://opsmesh.example.com",
        )
        assert resent.token_hash != first_digest
        assert resent.send_count == 2
        assert [call["idempotency_key"] for call in sender.calls] == [
            f"invitation-mail:{invitation.id}:1",
            f"invitation-mail:{invitation.id}:2",
        ]
        revoked = invitations.revoke_invitation(
            session, invitation_id=invitation.id, actor=admin
        )
        assert invitations.effective_status(revoked) == "revoked"
        with pytest.raises(InvitationStateError):
            invitations.accept_invitation(
                session,
                token=token,
                display_name="Invitee",
                password="correct-horse",
                account_opening=RecordingOpening(),
            )


def test_a_pending_invitation_opens_the_account(database_sessions) -> None:
    admin = AuditActor(actor_type="platform")
    opening = RecordingOpening()
    with database_sessions() as session, session.begin():
        invitation, token = invitations.create_invitation(
            session,
            email="invitee@example.com",
            invited_by=admin,
            sender=RecordingSender(),
        )
        opened = invitations.accept_invitation(
            session,
            token=token,
            display_name="Invitee",
            password="correct-horse",
            account_opening=opening,
        )
        assert opened.user_id is not None
        refreshed = invitations.get_invitation(session, invitation.id)
        assert invitations.effective_status(refreshed) == "accepted"
        assert refreshed.accepted_user_id == opened.user_id
    assert opening.calls == [
        {"email": "invitee@example.com", "display_name": "Invitee"}
    ]


def test_an_unknown_invitation_is_not_found(database_sessions) -> None:
    with database_sessions() as session, pytest.raises(NotFoundError):
        invitations.get_invitation(session, uuid4())


def test_a_second_pending_invitation_for_one_address_conflicts(database_sessions) -> None:
    admin = AuditActor(actor_type="platform")
    with database_sessions() as session, session.begin():
        invitations.create_invitation(
            session,
            email="invitee@example.com",
            invited_by=admin,
            sender=RecordingSender(),
        )
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        invitations.create_invitation(
            session,
            email="invitee@example.com",
            invited_by=admin,
            sender=RecordingSender(),
        )


def test_the_invitation_page_reports_its_total(database_sessions) -> None:
    admin = AuditActor(actor_type="platform")
    with database_sessions() as session, session.begin():
        for index in range(3):
            invitations.create_invitation(
                session,
                email=f"invitee-{index}@example.com",
                invited_by=admin,
                sender=RecordingSender(),
            )
    with database_sessions() as session:
        rows, total = invitations.list_invitations(
            session, page=PageParams(limit=2, offset=0)
        )
    assert total == 3
    assert len(rows) == 2


def test_an_accepted_invitation_is_never_revoked_or_resent(database_sessions) -> None:
    """The accept terminal state wins: later transitions are refused, not merged."""
    admin = AuditActor(actor_type="platform")
    with database_sessions() as session, session.begin():
        invitation, token = invitations.create_invitation(
            session,
            email="invitee@example.com",
            invited_by=admin,
            sender=RecordingSender(),
        )
        invitations.accept_invitation(
            session,
            token=token,
            display_name="Invitee",
            password="correct-horse",
            account_opening=RecordingOpening(),
        )
    with database_sessions() as session, pytest.raises(InvitationStateError), session.begin():
        invitations.revoke_invitation(session, invitation_id=invitation.id, actor=admin)
    with database_sessions() as session, pytest.raises(InvitationStateError), session.begin():
        invitations.resend_invitation(
            session,
            invitation_id=invitation.id,
            invited_by=admin,
            sender=RecordingSender(),
        )
    with database_sessions() as session:
        refreshed = invitations.get_invitation(session, invitation.id)
        assert invitations.effective_status(refreshed) == "accepted"
        assert refreshed.send_count == 1


TEST_CIPHER_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="

CONCURRENCY_TABLES = (
    "temporal_dispatches",
    "http_creation_receipts",
    "audit_append_receipts",
    "audit_chain_head",
    "audit_events",
    "audit_verifications",
    "account_invitations",
    "approval_requests",
    "login_sessions",
    "mail_intents",
    "security_events",
    "smtp_configurations",
    "storage_reservations",
    "token_reservations",
    "user_credentials",
    "user_quota_limits",
    "user_storage_accounts",
    "user_token_accounts",
    "users",
)


def reset_tables(engine) -> None:
    """Start one concurrency test from a known-empty database."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(CONCURRENCY_TABLES)))


@pytest.fixture
def clean_concurrency_tables(database_engine):
    """Empty the database around a concurrency test, before and after it."""
    reset_tables(database_engine)
    yield
    reset_tables(database_engine)


def concurrent_sessions(engine) -> sessionmaker[Session]:
    """One sessionmaker per worker thread over the shared test engine."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        info={
            "dispatch_cipher": SecretCipher(key=TEST_CIPHER_KEY, key_id="test"),
            "dispatch_trace_context": lambda: {},
        },
    )


def race_demotions(
    sessions: sessionmaker[Session], first_id: UUID, second_id: UUID
) -> dict[str, str]:
    """Demote both administrators at once; return each attempt's outcome."""
    start = threading.Event()
    outcomes: dict[str, str] = {}

    def demote(user_id: UUID, name: str) -> None:
        start.wait(timeout=10)
        try:
            with sessions() as session, session.begin():
                users.set_platform_roles(
                    session,
                    user_id=user_id,
                    roles=MEMBER_ROLES,
                    actor=AuditActor(actor_type="platform"),
                )
            outcomes[name] = "demoted"
        except ConflictError:
            outcomes[name] = "refused"

    threads = [
        threading.Thread(target=demote, args=(first_id, "first")),
        threading.Thread(target=demote, args=(second_id, "second")),
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


def test_concurrent_demotion_cannot_empty_the_platform(
    database_engine, clean_concurrency_tables
) -> None:
    """Two administrators demoting each other must not both be allowed.

    The last-admin guard decides inside the locked administrator set, so the
    second transaction sees the first one's committed change and is refused;
    a per-target lock alone lets both pass and removes every administrator.
    The race repeats because one round can serialize by timing alone and hide
    the write-skew window the guard exists for.
    """
    sessions = concurrent_sessions(database_engine)
    first_id, _token = open_account(
        sessions, email=ADMIN_EMAIL, display_name="First", password="correct-horse",
        roles=ADMIN_ROLES,
    )
    second_id, _token = open_account(
        sessions, email="second-admin@example.com", display_name="Second",
        password="correct-horse", roles=ADMIN_ROLES,
    )
    for round_index in range(3):
        if round_index:
            with sessions() as session, session.begin():
                for user_id in (first_id, second_id):
                    users.set_platform_roles(
                        session,
                        user_id=user_id,
                        roles=ADMIN_ROLES,
                        actor=AuditActor(actor_type="platform"),
                    )
        assert sorted(race_demotions(sessions, first_id, second_id).values()) == [
            "demoted",
            "refused",
        ]
    with sessions() as session:
        active_admins = session.scalar(
            select(func.count())
            .select_from(UserProfile)
            .where(
                UserProfile.status == "active",
                UserProfile.platform_roles.contains([users.PLATFORM_ADMINISTRATOR]),
            )
        )
    assert active_admins == 1


def test_concurrent_reservations_of_one_key_replay_one_reservation(
    database_engine, clean_concurrency_tables
) -> None:
    """The same idempotency key reserves once, however many callers race."""
    sessions = concurrent_sessions(database_engine)
    user_id, _token = open_account(
        sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    start = threading.Event()
    reservations: dict[str, object] = {}
    errors: dict[str, str] = {}

    def reserve(name: str) -> None:
        start.wait(timeout=10)
        try:
            with sessions() as session, session.begin():
                reservation = quotas.reserve_tokens(
                    session,
                    user_id=user_id,
                    amount=400,
                    idempotency_scope="run:race",
                    idempotency_key="reserve-race-1",
                )
            reservations[name] = reservation.id
        except Exception as exc:  # noqa: BLE001 - the race must not surface one
            errors[name] = type(exc).__name__

    threads = [
        threading.Thread(target=reserve, args=("first",)),
        threading.Thread(target=reserve, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == {}
    assert reservations["first"] == reservations["second"]
    with sessions() as session:
        rows = list(
            session.scalars(
                select(TokenReservation).where(
                    TokenReservation.idempotency_scope == "run:race"
                )
            )
        )
        account = session.get(UserTokenAccount, user_id)
    assert len(rows) == 1
    assert account is not None
    assert account.reserved_tokens == 400
