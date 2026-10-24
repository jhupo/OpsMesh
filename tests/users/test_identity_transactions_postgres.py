"""Credential transitions and administrator guards use committed account state."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import text

from opsmesh.platform.audit.contracts import AuditActor
from opsmesh.platform.audit.service import SecurityAuditRecorder
from opsmesh.shared.errors import ConflictError
from opsmesh.users import service as users
from opsmesh.users.auth.service import AuthService
from opsmesh.users.errors import AuthenticationError, InvalidCredentialsError
from opsmesh.users.models import UserProfile

PASSWORD = "original-password-123"
NEW_PASSWORD = "replacement-password-456"
ACTOR = AuditActor(actor_type="platform")


@pytest.fixture
def auth_service():
    return AuthService(security_events=SecurityAuditRecorder())


@pytest.fixture
def account(database_sessions):
    with database_sessions.begin() as session:
        profile = users.create_account(
            session, email="identity@example.test", display_name="Identity", password=PASSWORD
        )
        return profile.id


def _transition(sessions, pids, operation):
    with sessions.begin() as session:
        pids.put(session.scalar(text("SELECT pg_backend_pid()")))
        return operation(session)


def _wait_for_lock(sessions, pid, pending):
    deadline = monotonic() + 10
    with sessions() as session:
        while monotonic() < deadline:
            assert not pending.done(), "The account transition did not wait for the current holder"
            if session.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                return
            sleep(0.01)
    pytest.fail("The account transition did not reach its serialization lock")


@pytest.mark.parametrize("operation", ["login", "change_password"])
def test_old_password_is_rechecked_after_a_concurrent_change(
    database_sessions, auth_service, account, operation
):
    def use_old_password(session):
        if operation == "login":
            return auth_service.login(session, email="identity@example.test", password=PASSWORD)
        return auth_service.change_password(
            session, user_id=account, current_password=PASSWORD, new_password="third-password-789"
        )

    pids = Queue()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with database_sessions() as holder:
            auth_service.change_password(
                holder, user_id=account, current_password=PASSWORD, new_password=NEW_PASSWORD
            )
            pending = executor.submit(_transition, database_sessions, pids, use_old_password)
            try:
                _wait_for_lock(database_sessions, pids.get(timeout=10), pending)
            finally:
                holder.commit()
        with pytest.raises(InvalidCredentialsError):
            pending.result(timeout=10)


@pytest.mark.parametrize("operation", ["change_password", "delete_account"])
def test_account_transition_revokes_a_login_that_commits_while_it_waits(
    database_sessions, auth_service, account, operation
):
    def revoke_access(session):
        if operation == "delete_account":
            return users.delete_account(session, user_id=account, actor=ACTOR)
        return auth_service.change_password(
            session, user_id=account, current_password=PASSWORD, new_password=NEW_PASSWORD
        )

    pids = Queue()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with database_sessions() as holder:
            login = auth_service.login(holder, email="identity@example.test", password=PASSWORD)
            pending = executor.submit(_transition, database_sessions, pids, revoke_access)
            try:
                _wait_for_lock(database_sessions, pids.get(timeout=10), pending)
            finally:
                holder.commit()
        pending.result(timeout=10)
    with database_sessions() as session, pytest.raises(AuthenticationError):
        auth_service.authenticate(session, token=login.token)


def test_last_administrator_guard_refreshes_cached_roles_after_lock_wait(database_sessions):
    with database_sessions.begin() as session:
        admins = [
            users.create_user(
                session,
                email=f"admin-{index}@example.test",
                display_name="Administrator",
                platform_roles=frozenset({"admin"}),
            )
            for index in range(2)
        ]
        first_id, second_id = sorted(profile.id for profile in admins)

    ready = Queue()
    proceed = Event()

    def demote_first():
        with database_sessions.begin() as session:
            cached = session.get(UserProfile, second_id)
            assert cached.platform_roles == ["admin"]
            ready.put(session.scalar(text("SELECT pg_backend_pid()")))
            assert proceed.wait(timeout=10)
            users.set_platform_roles(
                session, user_id=first_id, roles=frozenset({"member"}), actor=ACTOR
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(demote_first)
        pid = ready.get(timeout=10)
        with database_sessions() as holder:
            try:
                users.set_platform_roles(
                    holder, user_id=second_id, roles=frozenset({"member"}), actor=ACTOR
                )
                proceed.set()
                _wait_for_lock(database_sessions, pid, pending)
            finally:
                proceed.set()
                holder.commit()
        with pytest.raises(ConflictError):
            pending.result(timeout=10)
    with database_sessions() as session:
        assert session.get(UserProfile, first_id).platform_roles == ["admin"]


def test_cached_profile_cannot_modify_a_concurrently_deleted_account(database_sessions, account):
    with database_sessions() as stale:
        cached = users.get_user(stale, account)
        assert cached.status == "active"
        with database_sessions.begin() as session:
            users.delete_account(session, user_id=account, actor=ACTOR)
        with pytest.raises(ConflictError):
            users.update_profile(stale, user_id=account, actor=ACTOR, display_name="Changed")
