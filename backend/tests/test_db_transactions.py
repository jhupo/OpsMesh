from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from backend.app.db import session as db_session_module
from backend.app.db.transactions import (
    TransactionRetryExhaustedError,
    TransactionRetryPolicy,
    run_in_transaction,
    sqlstate_from_error,
)


def test_run_in_transaction_commits_successful_operation() -> None:
    session = FakeSession()

    result = run_in_transaction(session, lambda: "ok", sleep=lambda _: None)

    assert result == "ok"
    assert session.commits == 1
    assert session.rollbacks == 0


def test_run_in_transaction_retries_retryable_database_error() -> None:
    session = FakeSession()
    sleeps: list[float] = []
    attempts = {"count": 0}

    def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _operational_error("40001")
        return "recovered"

    result = run_in_transaction(
        session,
        operation,
        retry_policy=TransactionRetryPolicy(
            max_attempts=3,
            base_delay_seconds=0.2,
            max_delay_seconds=1.0,
        ),
        sleep=sleeps.append,
    )

    assert result == "recovered"
    assert attempts["count"] == 2
    assert session.rollbacks == 1
    assert session.commits == 1
    assert sleeps == [0.2]


def test_run_in_transaction_exhausts_retryable_database_error() -> None:
    session = FakeSession()

    with pytest.raises(TransactionRetryExhaustedError) as raised:
        run_in_transaction(
            session,
            lambda: (_ for _ in ()).throw(_operational_error("40P01")),
            retry_policy=TransactionRetryPolicy(max_attempts=2, base_delay_seconds=0),
            sleep=lambda _: None,
        )

    assert raised.value.attempts == 2
    assert sqlstate_from_error(raised.value.last_error) == "40P01"
    assert session.rollbacks == 2
    assert session.commits == 0


def test_run_in_transaction_does_not_retry_non_retryable_database_error() -> None:
    session = FakeSession()

    with pytest.raises(OperationalError):
        run_in_transaction(
            session,
            lambda: (_ for _ in ()).throw(_operational_error("23505")),
            retry_policy=TransactionRetryPolicy(max_attempts=3),
            sleep=lambda _: None,
        )

    assert session.rollbacks == 1
    assert session.commits == 0


def test_sqlstate_from_error_supports_pgcode_attribute() -> None:
    error = OperationalError("SELECT 1", {}, PgCodeOnlyError("55P03"))

    assert sqlstate_from_error(error) == "55P03"


def test_get_db_session_rolls_back_on_dependency_exception(monkeypatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(db_session_module, "SessionLocal", lambda: session)
    dependency = db_session_module.get_db_session()

    assert next(dependency) is session
    with pytest.raises(RuntimeError, match="boom"):
        dependency.throw(RuntimeError("boom"))

    assert session.rollbacks == 1
    assert session.closes == 1


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


class SqlStateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class PgCodeOnlyError(Exception):
    def __init__(self, pgcode: str) -> None:
        super().__init__(pgcode)
        self.pgcode = pgcode


def _operational_error(sqlstate: str) -> OperationalError:
    return OperationalError("SELECT 1", {}, SqlStateError(sqlstate))
