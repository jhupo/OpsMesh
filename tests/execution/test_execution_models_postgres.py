"""Execution model tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise
and must run against a real PostgreSQL instance (never SQLite). They cover
the persistence contract of the execution core: per-session run
serialization, unique event keys, scoped operation receipts for model,
tool and MCP calls, the session status and history method constraints, the
environment and MCP connection lifecycles with their receipts, the
composite receipt key scoping, and the cascade semantics between a session
or environment and its receipts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from opsmesh.execution.runs.models import (
    OperationReceipt,
    Run,
    RunApproval,
    RunEvent,
)
from opsmesh.execution.runtime.models import (
    Environment,
    EnvironmentOperationReceipt,
    MCPConnection,
    MCPOperationReceipt,
)
from opsmesh.execution.sessions.models import SessionOperationReceipt, SessionRecord

SCOPE_ID = UUID("11111111-1111-2222-3333-444444444444")
OTHER_SCOPE_ID = UUID("55555555-1111-2222-3333-444444444444")
PRINCIPAL_ID = UUID("22222222-1111-2222-3333-444444444444")
SESSION_ID = UUID("33333333-1111-2222-3333-444444444444")
RUN_ID = UUID("44444444-1111-2222-3333-444444444444")
ENVIRONMENT_ID = UUID("66666666-1111-2222-3333-444444444444")
CONNECTION_ID = UUID("77777777-1111-2222-3333-444444444444")
APPROVAL_ID = UUID("88888888-1111-2222-3333-444444444444")
FINGERPRINT = "f" * 64


def create_session(
    sessions: sessionmaker, *, scope_id: UUID = SCOPE_ID, session_id: UUID = SESSION_ID
) -> SessionRecord:
    with sessions() as session, session.begin():
        record = SessionRecord(
            id=session_id, scope_id=scope_id, principal_id=PRINCIPAL_ID, owner_ref="workspace:1"
        )
        session.add(record)
    return record


def create_run(
    sessions: sessionmaker,
    *,
    sequence: int = 1,
    status: str = "queued",
    scope_id: UUID = SCOPE_ID,
    session_id: UUID = SESSION_ID,
    run_id: UUID = RUN_ID,
) -> Run:
    with sessions() as session, session.begin():
        record = Run(
            id=run_id,
            scope_id=scope_id,
            principal_id=PRINCIPAL_ID,
            owner_ref="workspace:1",
            session_id=session_id,
            sequence=sequence,
            status=status,
            request_ciphertext="encrypted-request",
            usage={"input_tokens": 0},
        )
        session.add(record)
    return record


def create_environment(sessions: sessionmaker) -> Environment:
    with sessions() as session, session.begin():
        record = Environment(
            id=ENVIRONMENT_ID,
            scope_id=SCOPE_ID,
            principal_id=PRINCIPAL_ID,
            owner_ref="workspace:1",
            status="pending",
            configuration={"backend": "local"},
            encrypted_state="encrypted-connection-state",
            encryption_key_id="key-1",
        )
        session.add(record)
    return record


def create_mcp_connection(
    sessions: sessionmaker,
    *,
    scope_id: UUID = SCOPE_ID,
    name: str = "search",
    connection_id: UUID = CONNECTION_ID,
) -> MCPConnection:
    with sessions() as session, session.begin():
        record = MCPConnection(
            id=connection_id,
            scope_id=scope_id,
            principal_id=PRINCIPAL_ID,
            owner_ref="workspace:1",
            name=name,
            transport="streamable_http",
            endpoint="https://mcp.example.com",
        )
        session.add(record)
    return record


def add_run_event(sessions: sessionmaker, *, sequence: int, event_key: str) -> None:
    with sessions() as session, session.begin():
        session.add(
            RunEvent(
                scope_id=SCOPE_ID,
                run_id=RUN_ID,
                sequence=sequence,
                event_key=event_key,
                kind="preparing",
                payload_ciphertext="encrypted",
                created_at=datetime.now(UTC),
            )
        )


class TestRuns:
    def test_runs_serialize_one_session_on_their_sequence(self, database_sessions) -> None:
        create_session(database_sessions)
        create_run(database_sessions, sequence=1)
        create_run(database_sessions, sequence=2, run_id=uuid4())
        with database_sessions() as session:
            stored = session.get(Run, RUN_ID)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.event_sequence == 0

    def test_one_session_cannot_hold_the_same_sequence_twice(self, database_sessions) -> None:
        create_session(database_sessions)
        create_run(database_sessions, sequence=1)
        with pytest.raises(IntegrityError):
            create_run(database_sessions, sequence=1, run_id=uuid4())

    def test_two_sessions_may_hold_the_same_sequence(self, database_sessions) -> None:
        create_session(database_sessions)
        create_run(database_sessions, sequence=1)
        other_session_id = uuid4()
        with database_sessions() as session, session.begin():
            session.add(
                SessionRecord(
                    id=other_session_id,
                    scope_id=SCOPE_ID,
                    principal_id=PRINCIPAL_ID,
                    owner_ref="workspace:1",
                )
            )
        create_run(database_sessions, sequence=1, session_id=other_session_id, run_id=uuid4())

    def test_an_unknown_run_status_is_refused(self, database_sessions) -> None:
        create_session(database_sessions)
        with pytest.raises(IntegrityError):
            create_run(database_sessions, status="paused")


class TestRunEvents:
    def test_events_are_unique_per_run_sequence(self, database_sessions) -> None:
        create_session(database_sessions)
        create_run(database_sessions)
        add_run_event(database_sessions, sequence=1, event_key=f"{RUN_ID}:1")
        add_run_event(database_sessions, sequence=2, event_key=f"{RUN_ID}:2")
        with pytest.raises(IntegrityError):
            add_run_event(database_sessions, sequence=2, event_key=f"{RUN_ID}:3")

    def test_a_redelivered_event_key_replays_instead_of_duplicating(
        self, database_sessions
    ) -> None:
        create_session(database_sessions)
        create_run(database_sessions)
        add_run_event(database_sessions, sequence=1, event_key=f"{RUN_ID}:1")
        with pytest.raises(IntegrityError):
            add_run_event(database_sessions, sequence=2, event_key=f"{RUN_ID}:1")


class TestRunApprovals:
    def test_the_approval_references_the_governance_request_without_a_foreign_key(
        self, database_sessions
    ) -> None:
        create_session(database_sessions)
        create_run(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                RunApproval(
                    scope_id=SCOPE_ID,
                    run_id=RUN_ID,
                    checkpoint_key=f"{RUN_ID}:checkpoint-1",
                    approval_id=APPROVAL_ID,
                    state_ciphertext="encrypted-state",
                    interruptions_ciphertext="encrypted-interruptions",
                )
            )
        with database_sessions() as session:
            stored = session.scalar(
                select(RunApproval).where(RunApproval.approval_id == APPROVAL_ID)
            )
        assert stored is not None
        assert stored.run_id == RUN_ID
        assert stored.approved is None


class TestOperationReceipts:
    def test_one_scope_cannot_hold_the_same_operation_twice(self, database_sessions) -> None:
        create_session(database_sessions)
        create_run(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                OperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="model:call-1",
                    run_id=RUN_ID,
                    fingerprint=FINGERPRINT,
                    kind="model",
                    status="started",
                )
            )
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                OperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="model:call-1",
                    run_id=RUN_ID,
                    fingerprint=FINGERPRINT,
                    kind="model",
                    status="started",
                )
            )

    def test_two_scopes_may_hold_the_same_operation_id(self, database_sessions) -> None:
        create_session(database_sessions)
        create_run(database_sessions)
        other_session_id = uuid4()
        other_run_id = uuid4()
        with database_sessions() as session, session.begin():
            session.add(
                SessionRecord(
                    id=other_session_id,
                    scope_id=OTHER_SCOPE_ID,
                    principal_id=PRINCIPAL_ID,
                    owner_ref="workspace:2",
                )
            )
            # Flush the session before the run that references it: without a
            # relationship the unit of work may order the two inserts either
            # way, and the run's foreign key must find its session row.
            session.flush()
            session.add(
                Run(
                    id=other_run_id,
                    scope_id=OTHER_SCOPE_ID,
                    principal_id=PRINCIPAL_ID,
                    owner_ref="workspace:2",
                    session_id=other_session_id,
                    sequence=1,
                    status="queued",
                    request_ciphertext="encrypted-request",
                    usage={},
                )
            )
        with database_sessions() as session, session.begin():
            session.add_all(
                [
                    OperationReceipt(
                        scope_id=SCOPE_ID,
                        operation_id="model:call-1",
                        run_id=RUN_ID,
                        fingerprint=FINGERPRINT,
                        kind="model",
                        status="started",
                    ),
                    OperationReceipt(
                        scope_id=OTHER_SCOPE_ID,
                        operation_id="model:call-1",
                        run_id=other_run_id,
                        fingerprint=FINGERPRINT,
                        kind="model",
                        status="started",
                    ),
                ]
            )

    @pytest.mark.parametrize("kind", ["model", "tool", "mcp"])
    def test_model_tool_and_mcp_calls_each_have_a_receipt(
        self, database_sessions, kind: str
    ) -> None:
        create_session(database_sessions)
        create_run(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                OperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id=f"{kind}:call-1",
                    run_id=RUN_ID,
                    fingerprint=FINGERPRINT,
                    kind=kind,
                    status="started",
                )
            )

    def test_an_unknown_receipt_kind_is_refused(self, database_sessions) -> None:
        create_session(database_sessions)
        create_run(database_sessions)
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                OperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="extension:call-1",
                    run_id=RUN_ID,
                    fingerprint=FINGERPRINT,
                    kind="extension",
                    status="started",
                )
            )


class TestSessions:
    def test_a_session_opens_at_its_first_sequence(self, database_sessions) -> None:
        create_session(database_sessions)
        with database_sessions() as session:
            stored = session.get(SessionRecord, SESSION_ID)
        assert stored is not None
        assert stored.status == "open"
        assert stored.next_sequence == 1

    def test_an_unknown_session_status_is_refused(self, database_sessions) -> None:
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                SessionRecord(
                    scope_id=SCOPE_ID,
                    principal_id=PRINCIPAL_ID,
                    owner_ref="workspace:1",
                    status="paused",
                )
            )

    def test_a_session_cannot_open_below_its_first_sequence(self, database_sessions) -> None:
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                SessionRecord(
                    scope_id=SCOPE_ID,
                    principal_id=PRINCIPAL_ID,
                    owner_ref="workspace:1",
                    next_sequence=0,
                )
            )

    @pytest.mark.parametrize("method", ["get_items", "add_items", "pop_item", "clear_session"])
    def test_every_history_method_has_a_receipt(
        self, database_sessions, method: str
    ) -> None:
        create_session(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                SessionOperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id=f"history:{method}",
                    session_id=SESSION_ID,
                    method=method,
                    fingerprint=FINGERPRINT,
                )
            )

    def test_an_unknown_history_method_is_refused(self, database_sessions) -> None:
        create_session(database_sessions)
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                SessionOperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="history:replace_items",
                    session_id=SESSION_ID,
                    method="replace_items",
                    fingerprint=FINGERPRINT,
                )
            )


class TestCascades:
    def test_deleting_a_session_removes_its_history_receipts(self, database_sessions) -> None:
        create_session(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                SessionOperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="history:get_items",
                    session_id=SESSION_ID,
                    method="get_items",
                    fingerprint=FINGERPRINT,
                )
            )
        with database_sessions() as session, session.begin():
            session.delete(session.get(SessionRecord, SESSION_ID))
        with database_sessions() as session:
            assert session.get(SessionRecord, SESSION_ID) is None
            assert (
                session.get(SessionOperationReceipt, (SCOPE_ID, "history:get_items")) is None
            )

    def test_a_run_with_receipts_cannot_be_deleted(self, database_sessions) -> None:
        create_session(database_sessions)
        create_run(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                OperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="model:call-1",
                    run_id=RUN_ID,
                    fingerprint=FINGERPRINT,
                    kind="model",
                    status="started",
                )
            )
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.delete(session.get(Run, RUN_ID))


class TestRuntime:
    def test_the_environment_keeps_its_encrypted_connection_state(
        self, database_sessions
    ) -> None:
        create_environment(database_sessions)
        with database_sessions() as session:
            stored = session.get(Environment, ENVIRONMENT_ID)
        assert stored is not None
        assert stored.encrypted_state == "encrypted-connection-state"
        assert stored.encryption_key_id == "key-1"
        assert stored.native_session_id is None

    def test_an_unknown_environment_status_is_refused(self, database_sessions) -> None:
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                Environment(
                    scope_id=SCOPE_ID,
                    principal_id=PRINCIPAL_ID,
                    owner_ref="workspace:1",
                    status="paused",
                    configuration={},
                )
            )

    def test_one_scope_cannot_name_the_same_mcp_connection_twice(
        self, database_sessions
    ) -> None:
        create_mcp_connection(database_sessions, name="search")
        with pytest.raises(IntegrityError):
            create_mcp_connection(database_sessions, name="search", connection_id=uuid4())

    def test_two_scopes_may_name_the_same_mcp_connection(self, database_sessions) -> None:
        create_mcp_connection(database_sessions, name="search")
        create_mcp_connection(
            database_sessions, scope_id=OTHER_SCOPE_ID, name="search", connection_id=uuid4()
        )

    def test_an_unknown_mcp_transport_is_refused(self, database_sessions) -> None:
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                MCPConnection(
                    scope_id=SCOPE_ID,
                    principal_id=PRINCIPAL_ID,
                    owner_ref="workspace:1",
                    name="search",
                    transport="carrier-pigeon",
                )
            )

    def test_an_unknown_mcp_status_is_refused(self, database_sessions) -> None:
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                MCPConnection(
                    scope_id=SCOPE_ID,
                    principal_id=PRINCIPAL_ID,
                    owner_ref="workspace:1",
                    name="search",
                    transport="stdio",
                    status="paused",
                )
            )

    @pytest.mark.parametrize("action", ["create", "resume", "close"])
    def test_the_environment_receipt_records_the_lifecycle_action(
        self, database_sessions, action: str
    ) -> None:
        create_environment(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                EnvironmentOperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id=f"environment:{action}",
                    environment_id=ENVIRONMENT_ID,
                    action=action,
                    fingerprint=FINGERPRINT,
                    status="started",
                )
            )

    def test_an_unknown_environment_receipt_action_is_refused(
        self, database_sessions
    ) -> None:
        create_environment(database_sessions)
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                EnvironmentOperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="environment:destroy",
                    environment_id=ENVIRONMENT_ID,
                    action="destroy",
                    fingerprint=FINGERPRINT,
                    status="started",
                )
            )

    def test_an_uncertain_environment_operation_keeps_its_receipt(
        self, database_sessions
    ) -> None:
        create_environment(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                EnvironmentOperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="environment:create",
                    environment_id=ENVIRONMENT_ID,
                    action="create",
                    fingerprint=FINGERPRINT,
                    status="uncertain",
                    encrypted_result="encrypted-result",
                    encryption_key_id="key-1",
                )
            )
        with database_sessions() as session:
            stored = session.get(
                EnvironmentOperationReceipt, (SCOPE_ID, "environment:create")
            )
        assert stored is not None
        assert stored.status == "uncertain"
        assert stored.encrypted_result == "encrypted-result"

    @pytest.mark.parametrize("action", ["connect", "discover", "close"])
    def test_the_mcp_receipt_records_the_connection_action(
        self, database_sessions, action: str
    ) -> None:
        create_mcp_connection(database_sessions)
        with database_sessions() as session, session.begin():
            session.add(
                MCPOperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id=f"mcp:{action}",
                    connection_id=CONNECTION_ID,
                    action=action,
                    fingerprint=FINGERPRINT,
                    status="completed",
                )
            )

    def test_an_unknown_mcp_receipt_action_is_refused(self, database_sessions) -> None:
        create_mcp_connection(database_sessions)
        with pytest.raises(IntegrityError), database_sessions() as session, session.begin():
            session.add(
                MCPOperationReceipt(
                    scope_id=SCOPE_ID,
                    operation_id="mcp:restart",
                    connection_id=CONNECTION_ID,
                    action="restart",
                    fingerprint=FINGERPRINT,
                    status="started",
                )
            )

    def test_deleting_an_environment_removes_only_its_own_receipts(
        self, database_sessions
    ) -> None:
        create_environment(database_sessions)
        create_mcp_connection(database_sessions)
        with database_sessions() as session, session.begin():
            session.add_all(
                [
                    EnvironmentOperationReceipt(
                        scope_id=SCOPE_ID,
                        operation_id="environment:create",
                        environment_id=ENVIRONMENT_ID,
                        action="create",
                        fingerprint=FINGERPRINT,
                        status="completed",
                    ),
                    MCPOperationReceipt(
                        scope_id=SCOPE_ID,
                        operation_id="mcp:connect",
                        connection_id=CONNECTION_ID,
                        action="connect",
                        fingerprint=FINGERPRINT,
                        status="completed",
                    ),
                ]
            )
        with database_sessions() as session, session.begin():
            session.delete(session.get(Environment, ENVIRONMENT_ID))
        with database_sessions() as session:
            assert session.get(Environment, ENVIRONMENT_ID) is None
            assert (
                session.get(EnvironmentOperationReceipt, (SCOPE_ID, "environment:create"))
                is None
            )
            assert session.get(MCPOperationReceipt, (SCOPE_ID, "mcp:connect")) is not None
