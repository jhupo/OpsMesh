"""S4 identity tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for identity: immediate session expiry
and revocation, the quota ledger, invitation token rotation and account
opening.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.governance.audit.service import AuditActor
from opsmesh.governance.security_events.service import SecurityEventLog
from opsmesh.identity.access import PlatformRole
from opsmesh.identity.auth.service import AuthService
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
from opsmesh.identity.users import service as users
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.pagination import PageParams

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
    """Register one account; returns the user id and its first session id."""
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
    return result.user.user_id, result.session_id


def test_registration_login_and_authentication(database_sessions) -> None:
    service = auth_service()
    with database_sessions() as session:
        with session.begin():
            result = service.register(
                session, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
            )
            token = result.session_id
        identity = service.authenticate(session, token=token)
        assert identity.email == MEMBER_EMAIL
        assert identity.has_platform_role("member")


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
    _user_id, session_id = open_account(
        database_sessions, email=MEMBER_EMAIL, display_name="Member", password="correct-horse"
    )
    from opsmesh.identity.auth.models import LoginSession

    with database_sessions() as session, session.begin():
        login_session = session.get(LoginSession, session_id)
        assert login_session is not None
        login_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with database_sessions() as session, pytest.raises(AuthenticationError):
        service.authenticate(session, token=session_id)


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
            user_id=uuid4(), session_id=uuid4(), expires_at=datetime.now(UTC)
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
