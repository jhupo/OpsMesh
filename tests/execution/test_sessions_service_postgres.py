"""Session service tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise
and must run against a real PostgreSQL instance (never SQLite). They drive
the session service and its native SDK item storage together:
the lifecycle, the visibility and scope rules, the native history
operations, atomic PostgreSQL mutation receipts, and the
guards that keep runs, closes and deletes from racing a history write.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.orchestration.contracts import (
    ExecutionScope,
    ExecutionSubject,
    RejectingScopeAdmission,
    ScopeAdmission,
)
from opsmesh.orchestration.sessions import history, service
from opsmesh.orchestration.sessions.access import session_scope_of
from opsmesh.orchestration.sessions.contracts import HistoryOperation, RejectingSessionRunActivity
from opsmesh.orchestration.sessions.history import HistoryGateway
from opsmesh.orchestration.sessions.models import SessionOperationReceipt, SessionRecord
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError

PRINCIPAL_ID = UUID("12121212-1111-2222-3333-444444444444")
OTHER_PRINCIPAL_ID = UUID("34343434-1111-2222-3333-444444444444")
CONFIGURATION_ID = UUID("56565656-1111-2222-3333-444444444444")

_T = TypeVar("_T")


class StubRunActivity:
    """The run guard with a switch the tests drive."""

    def __init__(self) -> None:
        self.active = False

    def has_active_runs(self, session: Session, *, session_id: UUID) -> bool:
        _ = session, session_id
        return self.active

    def has_retained_runs(self, session: Session, *, session_id: UUID) -> bool:
        return False


class RefusingAdmission:
    """An admission that refuses every external scope."""

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        _ = session, subject
        raise ForbiddenError("The execution scope is not admitted")


class PassingAdmission:
    """An admission that admits every scope."""

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        _ = session, subject, scope


@dataclass
class Harness:
    """One session service over the test database and the real history."""

    sessions: sessionmaker[Session]
    gateway: HistoryGateway
    activity: StubRunActivity

    def run(self, work: Coroutine[Any, Any, _T]) -> _T:
        return run_native(work)


def run_native(work: Coroutine[Any, Any, _T]) -> _T:
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        return runner.run(work)


@pytest.fixture
def harness(database_sessions: sessionmaker[Session], database_url: str) -> Iterator[Harness]:
    """All native history tables are owned by the initial migration."""
    yield Harness(
        sessions=database_sessions,
        gateway=HistoryGateway(database_sessions),
        activity=StubRunActivity(),
    )


def subject(principal_id: UUID = PRINCIPAL_ID) -> ExecutionSubject:
    return ExecutionSubject(user_id=principal_id)


def open_session(
    harness: Harness,
    *,
    principal_id: UUID = PRINCIPAL_ID,
    scope: ExecutionScope | None = None,
    admission: ScopeAdmission | None = None,
) -> SessionRecord:
    with harness.sessions() as session, session.begin():
        return service.create_session(
            session,
            subject=subject(principal_id),
            scope=scope if scope is not None else ExecutionScope(),
            admission=admission if admission is not None else RejectingScopeAdmission(),
        )


def close_session(harness: Harness, record: SessionRecord) -> SessionRecord:
    with harness.sessions() as session, session.begin():
        return service.close_session(
            session,
            subject=subject(),
            admission=RejectingScopeAdmission(),
            activity=harness.activity,
            session_id=record.id,
        )


def delete_session(harness: Harness, record: SessionRecord) -> None:
    harness.run(
        service.delete_session(
            harness.sessions,
            harness.gateway,
            subject=subject(),
            admission=RejectingScopeAdmission(),
            session_id=record.id,
            retention=harness.activity,
        )
    )


def read_history(harness: Harness, record: SessionRecord, *, limit: int | None = None) -> list:
    with harness.sessions() as session:
        return harness.run(
            service.read_history(
                session,
                harness.gateway,
                subject=subject(),
                admission=RejectingScopeAdmission(),
                session_id=record.id,
                limit=limit,
            )
        )


def append_history(
    harness: Harness, record: SessionRecord, items: list[dict[str, Any]], *, operation_id: str
) -> Any:
    return harness.run(
        service.operate_history(
            harness.sessions,
            harness.gateway,
            subject=subject(),
            admission=RejectingScopeAdmission(),
            activity=harness.activity,
            session_id=record.id,
            operation=HistoryOperation(method="add_items", items=tuple(items)),
            operation_id=operation_id,
        )
    )


def history_row_count(engine: Engine) -> int:
    with engine.begin() as connection:
        return connection.scalar(text("SELECT count(*) FROM execution_session_history_items"))


class TestSessionLifecycle:
    def test_a_session_opens_in_the_user_scope(self, harness: Harness) -> None:
        record = open_session(harness)
        assert record.status == "open"
        assert record.next_sequence == 1
        assert record.scope_id == PRINCIPAL_ID
        assert record.owner_ref == f"user:{PRINCIPAL_ID}"

    def test_a_configuration_session_stores_its_scope(self, harness: Harness) -> None:
        record = open_session(
            harness,
            scope=ExecutionScope(configuration_id=CONFIGURATION_ID),
            admission=PassingAdmission(),
        )
        assert record.scope_id == PRINCIPAL_ID
        assert record.owner_ref == f"user:{PRINCIPAL_ID}"
        assert record.configuration_id == CONFIGURATION_ID

    def test_concurrent_configuration_session_creation_reuses_the_users_session(
        self, harness: Harness
    ) -> None:
        def create(_: int) -> UUID:
            return open_session(
                harness,
                scope=ExecutionScope(configuration_id=CONFIGURATION_ID),
                admission=PassingAdmission(),
            ).id

        with ThreadPoolExecutor(max_workers=2) as workers:
            identifiers = list(workers.map(create, range(2)))
        assert identifiers[0] == identifiers[1]

    def test_configuration_users_have_private_sessions_and_independent_history_keys(
        self, harness: Harness
    ) -> None:
        records = [
            open_session(
                harness,
                principal_id=principal,
                scope=ExecutionScope(configuration_id=CONFIGURATION_ID),
                admission=PassingAdmission(),
            )
            for principal in (PRINCIPAL_ID, OTHER_PRINCIPAL_ID)
        ]
        assert records[0].id != records[1].id
        for record in records:
            harness.run(
                service.operate_history(
                    harness.sessions,
                    harness.gateway,
                    subject=subject(record.principal_id),
                    admission=PassingAdmission(),
                    activity=harness.activity,
                    session_id=record.id,
                    operation=HistoryOperation(
                        method="add_items",
                        items=({"role": "user", "content": str(record.principal_id)},),
                    ),
                    operation_id="same-client-key",
                )
            )
        with harness.sessions() as session, pytest.raises(NotFoundError):
            service.get_session(
                session,
                subject=subject(OTHER_PRINCIPAL_ID),
                admission=PassingAdmission(),
                session_id=records[0].id,
            )
        with harness.sessions() as session:
            for record in records:
                assert history.read_items(session, session_id=record.id) == [
                    {"role": "user", "content": str(record.principal_id)}
                ]

    def test_closing_configuration_session_cannot_be_bypassed_with_a_new_session(
        self, harness: Harness
    ) -> None:
        record = open_session(
            harness,
            scope=ExecutionScope(configuration_id=CONFIGURATION_ID),
            admission=PassingAdmission(),
        )
        harness.activity.active = True
        with harness.sessions() as session, session.begin():
            service.close_session(
                session,
                subject=subject(),
                admission=PassingAdmission(),
                activity=harness.activity,
                session_id=record.id,
            )
        with pytest.raises(ConflictError, match="still closing"):
            open_session(
                harness,
                scope=ExecutionScope(configuration_id=CONFIGURATION_ID),
                admission=PassingAdmission(),
            )

    def test_default_scope_is_personal(self) -> None:
        assert session_scope_of().is_user_scope

    def test_an_unadmitted_scope_refuses_the_creation(self, harness: Harness) -> None:
        with pytest.raises(ForbiddenError):
            open_session(
                harness,
                scope=ExecutionScope(configuration_id=CONFIGURATION_ID),
                admission=RefusingAdmission(),
            )

    def test_another_principals_session_is_invisible(self, harness: Harness) -> None:
        record = open_session(harness)
        with harness.sessions() as session, pytest.raises(NotFoundError):
            service.get_session(
                session,
                subject=subject(OTHER_PRINCIPAL_ID),
                admission=RejectingScopeAdmission(),
                session_id=record.id,
            )

    def test_a_revoked_configuration_session_is_refused(self, harness: Harness) -> None:
        record = open_session(
            harness,
            scope=ExecutionScope(configuration_id=CONFIGURATION_ID),
            admission=PassingAdmission(),
        )
        with harness.sessions() as session, pytest.raises(ForbiddenError):
            service.get_session(
                session,
                subject=subject(),
                admission=RefusingAdmission(),
                session_id=record.id,
            )

    def test_close_finalizes_a_session_without_active_runs(self, harness: Harness) -> None:
        record = open_session(harness)
        closed = close_session(harness, record)
        assert closed.status == "closed"

    def test_close_drains_behind_active_runs(self, harness: Harness) -> None:
        record = open_session(harness)
        harness.activity.active = True
        draining = close_session(harness, record)
        assert draining.status == "closing"
        harness.activity.active = False
        finalized = close_session(harness, record)
        assert finalized.status == "closed"

    def test_a_blocked_session_refuses_to_close(self, harness: Harness) -> None:
        record = open_session(harness)
        with harness.sessions() as session, session.begin():
            stored = session.get(SessionRecord, record.id)
            assert stored is not None
            stored.status = "blocked"
        with (
            pytest.raises(ConflictError) as refusal,
            harness.sessions() as session,
            session.begin(),
        ):
            service.close_session(
                session,
                subject=subject(),
                admission=RejectingScopeAdmission(),
                activity=harness.activity,
                session_id=record.id,
            )
        assert refusal.value.code == "session_blocked"

    def test_only_a_closed_session_can_be_deleted(self, harness: Harness) -> None:
        record = open_session(harness)
        with (
            pytest.raises(ConflictError) as refusal,
            harness.sessions() as session,
            session.begin(),
        ):
            harness.run(
                service.delete_session(
                    harness.sessions,
                    harness.gateway,
                    subject=subject(),
                    admission=RejectingScopeAdmission(),
                    session_id=record.id,
                    retention=harness.activity,
                )
            )
        assert refusal.value.code == "session_not_closed"
        close_session(harness, record)
        delete_session(harness, record)
        with harness.sessions() as session:
            assert session.get(SessionRecord, record.id) is None


class TestNativeHistory:
    def test_append_then_read_returns_the_items_in_order(
        self, harness: Harness, database_engine: Engine
    ) -> None:
        record = open_session(harness)
        append_history(harness, record, [{"role": "user", "content": "first"}], operation_id="a-1")
        append_history(
            harness, record, [{"role": "assistant", "content": "second"}], operation_id="a-2"
        )
        assert read_history(harness, record) == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
        assert history_row_count(database_engine) == 2

    def test_pop_removes_the_newest_item(self, harness: Harness) -> None:
        record = open_session(harness)
        append_history(harness, record, [{"role": "user", "content": "first"}], operation_id="a-1")
        append_history(harness, record, [{"role": "user", "content": "second"}], operation_id="a-2")
        popped = harness.run(
            service.operate_history(
                harness.sessions,
                harness.gateway,
                subject=subject(),
                admission=RejectingScopeAdmission(),
                activity=harness.activity,
                session_id=record.id,
                operation=HistoryOperation(method="pop_item"),
                operation_id="p-1",
            )
        )
        assert popped.item == {"role": "user", "content": "second"}
        assert read_history(harness, record) == [{"role": "user", "content": "first"}]

    def test_pop_of_an_empty_history_returns_no_item(self, harness: Harness) -> None:
        record = open_session(harness)
        popped = harness.run(
            service.operate_history(
                harness.sessions,
                harness.gateway,
                subject=subject(),
                admission=RejectingScopeAdmission(),
                activity=harness.activity,
                session_id=record.id,
                operation=HistoryOperation(method="pop_item"),
                operation_id="p-1",
            )
        )
        assert popped.item is None

    def test_clear_removes_the_whole_history(
        self, harness: Harness, database_engine: Engine
    ) -> None:
        record = open_session(harness)
        append_history(harness, record, [{"role": "user", "content": "first"}], operation_id="a-1")
        harness.run(
            service.operate_history(
                harness.sessions,
                harness.gateway,
                subject=subject(),
                admission=RejectingScopeAdmission(),
                activity=harness.activity,
                session_id=record.id,
                operation=HistoryOperation(method="clear_session"),
                operation_id="c-1",
            )
        )
        assert read_history(harness, record) == []
        assert history_row_count(database_engine) == 0

    def test_a_closed_session_keeps_its_history_readable(self, harness: Harness) -> None:
        record = open_session(harness)
        append_history(harness, record, [{"role": "user", "content": "first"}], operation_id="a-1")
        close_session(harness, record)
        assert read_history(harness, record) == [{"role": "user", "content": "first"}]

    def test_a_read_is_bounded_by_its_limit(self, harness: Harness) -> None:
        record = open_session(harness)
        for index in range(3):
            append_history(
                harness,
                record,
                [{"role": "user", "content": str(index)}],
                operation_id=f"a-{index}",
            )
        assert read_history(harness, record, limit=2) == [
            {"role": "user", "content": "1"},
            {"role": "user", "content": "2"},
        ]


class TestHistoryIdempotency:
    def test_a_replayed_append_does_not_store_twice(
        self, harness: Harness, database_engine: Engine
    ) -> None:
        record = open_session(harness)
        items = [{"role": "user", "content": "once"}]
        first = append_history(harness, record, items, operation_id="a-1")
        replayed = append_history(harness, record, items, operation_id="a-1")
        assert first.appended == replayed.appended == 1
        assert history_row_count(database_engine) == 1

    def test_a_replayed_pop_returns_the_same_item(self, harness: Harness) -> None:
        record = open_session(harness)
        append_history(harness, record, [{"role": "user", "content": "only"}], operation_id="a-1")
        first = harness.run(
            service.operate_history(
                harness.sessions,
                harness.gateway,
                subject=subject(),
                admission=RejectingScopeAdmission(),
                activity=harness.activity,
                session_id=record.id,
                operation=HistoryOperation(method="pop_item"),
                operation_id="p-1",
            )
        )
        replayed = harness.run(
            service.operate_history(
                harness.sessions,
                harness.gateway,
                subject=subject(),
                admission=RejectingScopeAdmission(),
                activity=harness.activity,
                session_id=record.id,
                operation=HistoryOperation(method="pop_item"),
                operation_id="p-1",
            )
        )
        assert first.item == replayed.item == {"role": "user", "content": "only"}
        assert read_history(harness, record) == []

    def test_a_key_reused_with_a_different_request_conflicts(self, harness: Harness) -> None:
        record = open_session(harness)
        append_history(harness, record, [{"role": "user", "content": "first"}], operation_id="a-1")
        with pytest.raises(ConflictError) as refusal:
            append_history(
                harness, record, [{"role": "user", "content": "other"}], operation_id="a-1"
            )
        assert refusal.value.code == "idempotency_key_reused"

    def test_a_failed_history_write_rolls_back_items_and_receipt(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = open_session(harness)
        publish = history.publish_batch

        def fail_after_publication(*args, **kwargs):
            publish(*args, **kwargs)
            raise RuntimeError("Simulated transaction failure after item publication")

        with monkeypatch.context() as scoped:
            scoped.setattr(history, "publish_batch", fail_after_publication)
            with pytest.raises(RuntimeError):
                append_history(
                    harness, record, [{"role": "user", "content": "x"}], operation_id="a-1"
                )
        with harness.sessions() as session:
            receipt = session.get(SessionOperationReceipt, ("a-1", record.id))
            assert receipt is None
            assert session.get(SessionRecord, record.id).history_revision == 0
        assert read_history(harness, record) == []
        append_history(harness, record, [{"role": "user", "content": "x"}], operation_id="a-1")
        assert read_history(harness, record) == [{"role": "user", "content": "x"}]


class TestHistoryGuards:
    def test_a_closed_session_refuses_a_mutation(self, harness: Harness) -> None:
        record = open_session(harness)
        close_session(harness, record)
        with pytest.raises(ConflictError) as refusal:
            append_history(harness, record, [{"role": "user", "content": "x"}], operation_id="a-1")
        assert refusal.value.code == "session_not_open"

    def test_a_session_with_active_runs_refuses_a_mutation(self, harness: Harness) -> None:
        record = open_session(harness)
        harness.activity.active = True
        with pytest.raises(ConflictError) as refusal:
            append_history(harness, record, [{"role": "user", "content": "x"}], operation_id="a-1")
        assert refusal.value.code == "session_has_active_runs"

    def test_an_unassembled_guard_refuses_a_mutation(self, harness: Harness) -> None:
        record = open_session(harness)
        with pytest.raises(ConflictError):
            harness.run(
                service.operate_history(
                    harness.sessions,
                    harness.gateway,
                    subject=subject(),
                    admission=RejectingScopeAdmission(),
                    activity=RejectingSessionRunActivity(),
                    session_id=record.id,
                    operation=HistoryOperation(method="add_items", items=({"role": "user"},)),
                    operation_id="a-1",
                )
            )


class TestDeleteWithHistory:
    def test_delete_removes_the_session_and_its_native_history(
        self, harness: Harness, database_engine: Engine
    ) -> None:
        record = open_session(harness)
        append_history(harness, record, [{"role": "user", "content": "first"}], operation_id="a-1")
        close_session(harness, record)
        delete_session(harness, record)
        assert history_row_count(database_engine) == 0
        with database_engine.begin() as connection:
            sessions_left = connection.scalar(
                text("SELECT count(*) FROM execution_session_history_batches")
            )
        assert sessions_left == 0
