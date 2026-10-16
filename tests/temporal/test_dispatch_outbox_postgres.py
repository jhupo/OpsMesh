"""S2 dispatch Outbox tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must be executed against a real PostgreSQL instance (never SQLite).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session

from opsmesh.temporal.dispatch.contracts import (
    DispatchOperation,
    DispatchRequest,
    WorkflowStartOptions,
)
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from opsmesh.temporal.dispatch.outbox import (
    TemporalOutboxPublisher,
    stage_dispatch,
    stage_signal,
)
from tests.conftest import dispatch_cipher


def start_request(workflow_id: str) -> DispatchRequest:
    return DispatchRequest(
        dispatch_id=uuid4(),
        workflow_id=workflow_id,
        operation=DispatchOperation.START,
        execution={"input": "value"},
        workflow_name="OpsMeshTestWorkflow",
        start_options=WorkflowStartOptions(),
    )


def count_rows(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(select(func.count()).select_from(TemporalOutboxEntry)) or 0
        )


def load_row(engine: Engine) -> TemporalOutboxEntry | None:
    with Session(engine) as session:
        return session.scalar(select(TemporalOutboxEntry))


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start_workflow(self, name, execution, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(name)


class BlockingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.release = asyncio.Event()

    async def start_workflow(self, name, execution, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(name)
        await self.release.wait()


class FailingClient:
    async def start_workflow(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("temporal unavailable")


def publisher(sessions: async_sessionmaker, client: object) -> TemporalOutboxPublisher:
    return TemporalOutboxPublisher(
        sessions,
        client,  # type: ignore[arg-type]
        task_queue="test",
        concurrency=4,
        cipher=dispatch_cipher(),
    )


def test_staged_dispatch_is_invisible_after_rollback(database_sessions, database_engine) -> None:
    with database_sessions() as session:
        stage_dispatch(session, start_request("rollback-run"))
        session.flush()
        staged_count = session.scalar(select(func.count()).select_from(TemporalOutboxEntry))
        assert staged_count == 1
        session.rollback()
    assert count_rows(database_engine) == 0


def test_committed_dispatch_persists_one_row(database_sessions, database_engine) -> None:
    with database_sessions() as session:
        stage_dispatch(session, start_request("commit-run"))
        session.commit()
    assert count_rows(database_engine) == 1


def test_repeated_staging_of_same_dispatch_is_idempotent(
    database_sessions, database_engine
) -> None:
    request = start_request("idempotent-run")
    with database_sessions() as session:
        stage_dispatch(session, request)
        session.commit()
    with database_sessions() as session:
        stage_dispatch(session, request)
        session.commit()
    assert count_rows(database_engine) == 1


def test_staging_same_id_with_different_command_is_rejected(
    database_sessions, database_engine
) -> None:
    request = start_request("conflict-run")
    with database_sessions() as session:
        stage_dispatch(session, request)
        session.commit()
    conflicting = DispatchRequest(
        dispatch_id=request.dispatch_id,
        workflow_id="different-run",
        operation=DispatchOperation.SIGNAL,
        signal_name="resume",
        signal_args=(),
    )
    with database_sessions() as session, pytest.raises(ValueError):
        stage_dispatch(session, conflicting)
        session.commit()


def test_signal_staging_requires_no_execution_payload(database_sessions, database_engine) -> None:
    with database_sessions() as session:
        stage_signal(
            session,
            dispatch_id=uuid4(),
            workflow_id="signal-run",
            signal_name="resume",
            signal_args=({"approved": True},),
        )
        session.commit()
    assert count_rows(database_engine) == 1


def test_publisher_marks_published_rows(
    database_sessions, async_database_sessions, database_engine
) -> None:
    with database_sessions() as session:
        stage_dispatch(session, start_request("publish-run"))
        session.commit()

    client = RecordingClient()
    delivered = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered == 1
    assert client.calls == ["OpsMeshTestWorkflow"]
    row = load_row(database_engine)
    assert row is not None
    assert row.published_at is not None
    assert row.claim_token is None


def test_failed_delivery_schedules_backoff(
    database_sessions, async_database_sessions, database_engine
) -> None:
    with database_sessions() as session:
        stage_dispatch(session, start_request("backoff-run"))
        session.commit()

    delivered = asyncio.run(publisher(async_database_sessions, FailingClient()).publish_pending())
    assert delivered == 0
    row = load_row(database_engine)
    assert row is not None
    assert row.attempts == 1
    assert row.available_at > datetime.now(UTC)
    assert row.available_at <= datetime.now(UTC) + timedelta(seconds=5)


def test_concurrent_publishers_claim_exclusively(
    database_sessions, async_database_sessions, database_engine
) -> None:
    with database_sessions() as session:
        stage_dispatch(session, start_request("exclusive-run"))
        session.commit()

    client = BlockingClient()

    async def run_both() -> tuple[list[int], bool]:
        first = asyncio.create_task(publisher(async_database_sessions, client).publish_pending())
        second = asyncio.create_task(publisher(async_database_sessions, client).publish_pending())
        await asyncio.sleep(0.5)
        claimed_once = len(client.calls) == 1
        client.release.set()
        return await asyncio.gather(first, second), claimed_once

    delivered, claimed_once = asyncio.run(run_both())
    assert claimed_once, "both publishers delivered the same dispatch"
    assert sorted(delivered) == [0, 1], "exactly one publisher delivered the claimed dispatch"
    assert len(client.calls) == 1
    row = load_row(database_engine)
    assert row is not None
    assert row.published_at is not None
