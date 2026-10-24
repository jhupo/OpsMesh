"""Host-owner administration preserves account and login transaction boundaries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.bootstrap.accounts import administrator_handoff
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.shared.errors import ConflictError
from opsmesh.users.auth.models import LoginSession, UserCredential
from opsmesh.users.auth.passwords import verify_password
from opsmesh.users.auth.service import token_digest
from opsmesh.users.models import UserProfile
from opsmesh.users.quotas.models import UserQuotaLimit
from opsmesh.users.service import create_user


def handoff(sessions, *, reset=False):
    with sessions() as session, session.begin():
        return administrator_handoff(
            session,
            reset=reset,
            email="admin@example.com",
            token_limit=1000,
            provision_resources=lambda session, user_id: None,
        )


def test_concurrent_initialization_hands_out_only_one_password(database_sessions):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: handoff(database_sessions), range(2)))
    assert sorted(item["status"] for item in results) == ["already_initialized", "created"]
    assert sum("password" in item for item in results) == 1
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(UserProfile)) == 1


def test_explicit_reset_revokes_logins_and_preserves_quota(database_sessions):
    created = handoff(database_sessions)
    user_id = UUID(created["user_id"])
    with database_sessions() as session, session.begin():
        login = LoginSession(
            user_id=user_id,
            token_hash=token_digest(uuid4()),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(login)
        session.flush()
        login_id = login.id
    reset = handoff(database_sessions, reset=True)
    assert reset["password"] != created["password"]
    with database_sessions() as session:
        credential = session.scalar(select(UserCredential).where(UserCredential.user_id == user_id))
        assert verify_password(reset["password"], credential.password_hash)
        assert not verify_password(created["password"], credential.password_hash)
        assert session.get(LoginSession, login_id).revoked_at is not None
        assert session.get(UserQuotaLimit, user_id).token_limit == 1000
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "administrator.password_reset")
            )
            == 1
        )


def test_host_bootstrap_does_not_promote_an_existing_member(database_sessions):
    with database_sessions() as session, session.begin():
        member = create_user(session, email="admin@example.com", display_name="Member")
        user_id = member.id
    for reset in (False, True):
        with pytest.raises(ConflictError):
            handoff(database_sessions, reset=reset)
    with database_sessions() as session:
        assert session.get(UserProfile, user_id).platform_roles == ["member"]
        assert session.scalar(select(func.count()).select_from(UserCredential)) == 0
