"""One user owns all quantity ledgers; retries and rollback do not duplicate holds."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from opsmesh.platform.audit.contracts import AuditActor
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.users import service as users
from opsmesh.users.errors import QuotaExceededError, QuotaReservationError
from opsmesh.users.quotas import service as quotas
from opsmesh.users.quotas.models import UserCapacityAccount, UserQuotaLimit, UserTokenAccount


@pytest.fixture
def quota_user(database_sessions):
    with database_sessions.begin() as session:
        user = users.create_user(session, email=f"{uuid4()}@example.test", display_name="Quota")
        quotas.ensure_ledger(session, user_id=user.id, token_limit=100)
        quotas.ensure_capacity_accounts(
            session, user_id=user.id, limits={"concurrency": 1, "environment": 2}
        )
        return users.identity_of(user)


def test_capacity_replay_release_and_transaction_rollback(database_sessions, quota_user):
    arguments = dict(
        user_id=quota_user.user_id,
        kind="concurrency",
        amount=1,
        idempotency_scope="test",
        idempotency_key="first",
    )
    with database_sessions.begin() as session:
        reservation = quotas.reserve_capacity(session, **arguments)
        identifier = reservation.id
        assert quotas.reserve_capacity(session, **arguments).id == identifier
    with database_sessions() as session, pytest.raises(quotas.CapacityExceededError):
        quotas.reserve_capacity(session, **{**arguments, "idempotency_key": "second"})
    with database_sessions.begin() as session:
        quotas.release_capacity(session, user_id=quota_user.user_id, reservation_id=identifier)
        quotas.release_capacity(session, user_id=quota_user.user_id, reservation_id=identifier)
    with database_sessions() as session:
        quotas.reserve_capacity(session, **{**arguments, "idempotency_key": "rollback"})
        session.rollback()
    with database_sessions() as session:
        assert (
            session.get(UserCapacityAccount, (quota_user.user_id, "concurrency")).reserved_value
            == 0
        )
        with pytest.raises(ConflictError):
            quotas.reserve_capacity(session, **{**arguments, "amount": 2})


def test_member_cannot_raise_own_capacity_limit(database_sessions, quota_user):
    with database_sessions() as session, pytest.raises(ForbiddenError):
        quotas.set_capacity_limit(
            session,
            actor=quota_user,
            user_id=quota_user.user_id,
            kind="concurrency",
            limit_value=None,
        )


def test_token_same_settlement_replays_actual_usage_once(database_sessions, quota_user):
    with database_sessions.begin() as session:
        reservation = quotas.reserve_tokens(
            session,
            user_id=quota_user.user_id,
            amount=50,
            idempotency_scope="test",
            idempotency_key="tokens",
        )
        identifier = reservation.id
        quotas.settle_tokens(session, reservation_id=identifier, actual_amount=17)
        quotas.settle_tokens(session, reservation_id=identifier, actual_amount=17)
    with database_sessions() as session:
        account = session.get(UserTokenAccount, quota_user.user_id)
        assert (account.used_tokens, account.reserved_tokens) == (17, 0)
        with pytest.raises(QuotaReservationError):
            quotas.settle_tokens(session, reservation_id=identifier, actual_amount=18)


def test_concurrent_token_holds_cannot_exceed_user_allowance(database_sessions, quota_user):
    def reserve(index):
        try:
            with database_sessions.begin() as session:
                quotas.reserve_tokens(
                    session,
                    user_id=quota_user.user_id,
                    amount=60,
                    idempotency_scope="concurrent",
                    idempotency_key=str(index),
                )
            return True
        except QuotaExceededError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == [False, True]
    with database_sessions() as session:
        assert session.get(UserTokenAccount, quota_user.user_id).reserved_tokens == 60


def test_cached_quota_limits_do_not_override_current_administrator_limit(
    database_sessions, quota_user
):
    with database_sessions() as stale:
        cached = stale.get(UserQuotaLimit, quota_user.user_id)
        assert cached.token_limit == 100
        with database_sessions.begin() as session:
            quotas.set_limits(
                session,
                user_id=quota_user.user_id,
                actor=AuditActor.from_user(quota_user),
                token_limit=10,
            )
        with pytest.raises(QuotaExceededError):
            quotas.reserve_tokens(
                stale,
                user_id=quota_user.user_id,
                amount=11,
                idempotency_scope="cached",
                idempotency_key="tokens",
            )
