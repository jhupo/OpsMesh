"""S2 dispatch Outbox tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must be executed against a real PostgreSQL instance (never SQLite).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session
from temporalio.client import WorkflowExecutionStatus

from opsmesh.temporal.dispatch.contracts import (
    DispatchOperation,
    DispatchRequest,
    WorkflowStartOptions,
)
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from opsmesh.temporal.dispatch.outbox import (
    TemporalOutboxPublisher,
    stage_cancellation,
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


def cancel_request(workflow_id: str) -> DispatchRequest:
    return DispatchRequest(
        dispatch_id=uuid4(),
        workflow_id=workflow_id,
        operation=DispatchOperation.CANCEL,
    )


def signal_request(workflow_id: str) -> DispatchRequest:
    return DispatchRequest(
        dispatch_id=uuid4(),
        workflow_id=workflow_id,
        operation=DispatchOperation.SIGNAL,
        signal_name="resume",
        signal_args=({"approved": True},),
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
        self.signal_calls: list[tuple[str, tuple[object, ...]]] = []
        self.running: set[str] = set()

    async def start_workflow(self, name, execution, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(name)
        self.running.add(kwargs.get("id", ""))

    def get_workflow_handle(self, workflow_id):  # noqa: ANN001, ANN201
        client = self

        class Handle:
            async def describe(self, **kwargs):  # noqa: ANN003, ANN201
                class Description:
                    status = (
                        WorkflowExecutionStatus.RUNNING
                        if workflow_id in client.running
                        else WorkflowExecutionStatus.COMPLETED
                    )

                return Description()

            async def signal(self, name, **kwargs):  # noqa: ANN001, ANN003
                client.signal_calls.append((name, tuple(kwargs.get("args", ()))))
                client.running.discard(workflow_id)

            async def cancel(self, **kwargs):  # noqa: ANN003
                client.running.discard(workflow_id)

        return Handle()


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
    assert row.published_at is None, "a failed delivery must release its ordering decision"
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


def test_cancel_arriving_before_start_defers_until_the_start_is_delivered(
    database_sessions, async_database_sessions, database_engine
) -> None:
    workflow_id = "cancel-before-start-run"
    with database_sessions() as session:
        stage_dispatch(session, start_request(workflow_id))
        stage_cancellation(session, dispatch_id=uuid4(), workflow_id=workflow_id)
        session.commit()

    # Hold the start back so the cancel is claimable while the start is undelivered.
    postpone_rows(database_engine, DispatchOperation.START, timedelta(seconds=120))

    client = RecordingClient()
    delivered = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered == 0, "the cancel defers while its start command is still undelivered"
    assert client.calls == []
    deferred_row = load_cancel_row(database_engine, workflow_id)
    assert deferred_row is not None
    assert deferred_row.published_at is None
    assert deferred_row.last_error_code == "awaiting_sibling_command"

    release_rows(database_engine, DispatchOperation.START)
    delivered_start = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered_start == 1, "the start is delivered once it is due"
    assert client.calls == ["OpsMeshTestWorkflow"]
    assert workflow_id in client.running, "the workflow must be running after its start"

    release_rows(database_engine, DispatchOperation.CANCEL)
    delivered_cancel = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered_cancel == 1, "the deferred cancel cancels the running workflow"
    assert workflow_id not in client.running
    cancel_row = load_cancel_row(database_engine, workflow_id)
    assert cancel_row is not None
    assert cancel_row.published_at is not None


def test_start_is_never_delivered_after_its_cancel_was_published(
    database_sessions, async_database_sessions, database_engine
) -> None:
    workflow_id = "cancel-without-start-run"
    with database_sessions() as session:
        stage_cancellation(session, dispatch_id=uuid4(), workflow_id=workflow_id)
        session.commit()

    client = RecordingClient()
    delivered = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered == 1, "a cancel for a workflow that was never started completes"
    assert client.calls == []

    # A start staged afterwards for the same workflow must not start it.
    with database_sessions() as session:
        stage_dispatch(session, start_request(workflow_id))
        session.commit()
    delivered_again = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered_again == 1, "the superseded start is recorded as delivered"
    assert client.calls == [], "a cancelled workflow must never be started"


def test_cancel_decided_before_its_start_is_staged_supersedes_the_start(
    database_sessions, async_database_sessions, database_engine
) -> None:
    """The start command commits only while the cancel is being delivered.

    The cancel decision finds no start command at all, so it publishes; the
    start that commits afterwards must observe that decision instead of
    starting the workflow behind the completed cancellation.
    """
    workflow_id = "cancel-decided-then-staged-run"
    with database_sessions() as session:
        stage_cancellation(session, dispatch_id=uuid4(), workflow_id=workflow_id)
        session.commit()

    class StagingClient:
        """Commits the start command while the cancel is being delivered."""

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.running: set[str] = set()
            self.staged = False

        async def start_workflow(self, name, execution, **kwargs):  # noqa: ANN001, ANN003
            self.calls.append(name)
            self.running.add(kwargs.get("id", ""))

        def get_workflow_handle(self, workflow_id):  # noqa: ANN001, ANN201
            client = self

            class Handle:
                async def describe(self, **kwargs):  # noqa: ANN003, ANN201
                    class Description:
                        # The workflow is running, so the cancel is delivered
                        # through the handle instead of short-circuiting.
                        status = WorkflowExecutionStatus.RUNNING

                    return Description()

                async def cancel(self, **kwargs):  # noqa: ANN003, ANN201
                    if not client.staged:
                        client.staged = True
                        with database_sessions() as session:
                            stage_dispatch(session, start_request(workflow_id))
                            session.commit()
                    client.running.discard(workflow_id)

            return Handle()

    client = StagingClient()
    delivered = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered == 2, "the cancel completes and the late start is superseded"
    assert client.staged, "the start command must have committed during the cancel delivery"
    assert client.calls == [], "a workflow with a decided cancel must never be started"
    delivered_again = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered_again == 0, "both commands reached a durable decision"


def test_concurrent_start_and_cancel_settle_without_a_running_workflow(
    database_sessions, async_database_sessions, database_engine
) -> None:
    workflow_id = "concurrent-start-cancel-run"
    with database_sessions() as session:
        stage_dispatch(session, start_request(workflow_id))
        stage_cancellation(session, dispatch_id=uuid4(), workflow_id=workflow_id)
        session.commit()

    client = RecordingClient()

    async def run_both() -> list[int]:
        first = asyncio.create_task(publisher(async_database_sessions, client).publish_pending())
        second = asyncio.create_task(publisher(async_database_sessions, client).publish_pending())
        return await asyncio.gather(first, second)

    delivered = sum(asyncio.run(run_both()))
    # A cancel deferred behind the start retries after its backoff.
    release_rows(database_engine, DispatchOperation.CANCEL)
    delivered += asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered == 2, "both commands must reach a durable decision"
    assert workflow_id not in client.running, (
        "the workflow must never stay running after its cancel is decided"
    )
    with Session(database_engine) as session:
        decisions = session.scalars(
            select(TemporalOutboxEntry.published_at).where(
                TemporalOutboxEntry.workflow_id == workflow_id
            )
        ).all()
    assert len(decisions) == 2 and all(decision is not None for decision in decisions)


def test_interrupted_delivery_stays_claimable_and_is_retried(
    database_sessions, async_database_sessions, database_engine
) -> None:
    """A delivery cancelled before the Temporal call must not be recorded as
    published: the command stays claimable once the lease expires and delivers
    on a later pass."""
    workflow_id = "interrupted-delivery-run"
    with database_sessions() as session:
        stage_dispatch(session, start_request(workflow_id))
        session.commit()

    class CancellingClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def start_workflow(self, name, execution, **kwargs):  # noqa: ANN001, ANN003
            self.calls.append(name)
            raise asyncio.CancelledError

    client = CancellingClient()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(publisher(async_database_sessions, client).publish_pending())
    row = load_row(database_engine)
    assert row is not None
    assert row.published_at is None, "an interrupted delivery must not be recorded as published"
    assert client.calls == ["OpsMeshTestWorkflow"]

    # The claim lease expires, so the command is claimable again.
    release_rows(database_engine, DispatchOperation.START)
    expire_claims(database_engine)
    delivered = asyncio.run(publisher(async_database_sessions, RecordingClient()).publish_pending())
    assert delivered == 1, "the interrupted command must be delivered on a later pass"
    delivered_row = load_row(database_engine)
    assert delivered_row is not None
    assert delivered_row.published_at is not None


def test_cancel_defers_while_its_start_delivery_is_in_flight(
    database_sessions, async_database_sessions, database_engine
) -> None:
    """While the start RPC has not taken effect the Workflow does not exist yet;
    a cancel that completed against that absence would leave the Workflow
    running once the start lands, so the cancel must wait for the confirmation."""
    workflow_id = "cancel-during-start-delivery-run"
    with database_sessions() as session:
        stage_dispatch(session, start_request(workflow_id))
        stage_cancellation(session, dispatch_id=uuid4(), workflow_id=workflow_id)
        session.commit()

    class InFlightClient:
        """The start blocks on an event; the workflow only exists afterwards."""

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.cancelled: list[str] = []
            self.running: set[str] = set()
            self.release = asyncio.Event()

        async def start_workflow(self, name, execution, **kwargs):  # noqa: ANN001, ANN003
            self.calls.append(name)
            await self.release.wait()
            self.running.add(kwargs.get("id", ""))

        def get_workflow_handle(self, workflow_id):  # noqa: ANN001, ANN201
            client = self

            class Handle:
                async def describe(self, **kwargs):  # noqa: ANN003, ANN201
                    class Description:
                        status = (
                            WorkflowExecutionStatus.RUNNING
                            if workflow_id in client.running
                            else WorkflowExecutionStatus.COMPLETED
                        )

                    return Description()

                async def cancel(self, **kwargs):  # noqa: ANN003, ANN201
                    client.cancelled.append(workflow_id)
                    client.running.discard(workflow_id)

            return Handle()

    client = InFlightClient()

    async def run_both() -> list[int]:
        first = asyncio.create_task(publisher(async_database_sessions, client).publish_pending())
        second = asyncio.create_task(publisher(async_database_sessions, client).publish_pending())
        await asyncio.sleep(0.5)
        # The start delivery is still in flight here.
        client.release.set()
        return await asyncio.gather(first, second)

    delivered = asyncio.run(run_both())
    assert sorted(delivered) == [0, 1], "only the start delivers while its cancel waits"
    cancel_row = load_cancel_row(database_engine, workflow_id)
    assert cancel_row is not None
    assert cancel_row.published_at is None, (
        "the cancel must not complete while the start delivery is in flight"
    )
    assert cancel_row.last_error_code == "awaiting_sibling_command"
    assert client.cancelled == []

    # Once the start is confirmed, the cancel cancels the running workflow.
    release_rows(database_engine, DispatchOperation.CANCEL)
    delivered_cancel = asyncio.run(publisher(async_database_sessions, client).publish_pending())
    assert delivered_cancel == 1
    assert client.cancelled == [workflow_id], "the cancel must reach the running workflow"
    assert workflow_id not in client.running


def test_start_staged_while_the_cancel_delivery_is_in_flight_is_superseded(
    database_sessions, async_database_sessions, database_engine
) -> None:
    """The cancel intent is durable before its delivery is attempted. A start
    that commits while the cancel delivery is still in flight must be blocked
    before the cancel is confirmed, even though the cancel itself completes
    against a Workflow that does not exist yet."""
    workflow_id = "cancel-in-flight-then-staged-run"
    with database_sessions() as session:
        stage_cancellation(session, dispatch_id=uuid4(), workflow_id=workflow_id)
        session.commit()

    class BlockingDescribeClient:
        """The cancel delivery signals entry into describe, then blocks."""

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.running: set[str] = set()
            self.entered_describe = asyncio.Event()
            self.release = asyncio.Event()

        async def start_workflow(self, name, execution, **kwargs):  # noqa: ANN001, ANN003
            self.calls.append(name)
            self.running.add(kwargs.get("id", ""))

        def get_workflow_handle(self, workflow_id):  # noqa: ANN001, ANN201
            client = self

            class Handle:
                async def describe(self, **kwargs):  # noqa: ANN003, ANN201
                    # The cancel decision is durable and its RPC is in flight.
                    client.entered_describe.set()
                    await client.release.wait()

                    class Description:
                        status = WorkflowExecutionStatus.COMPLETED

                    return Description()

                async def cancel(self, **kwargs):  # noqa: ANN003, ANN201
                    client.running.discard(workflow_id)

            return Handle()

    client = BlockingDescribeClient()

    async def run_interleaved() -> tuple[int, int]:
        # Publisher A claims the cancel and blocks inside its RPC.
        cancel_task = asyncio.create_task(
            publisher(async_database_sessions, client).publish_pending()
        )
        await client.entered_describe.wait()
        # The start commits while the cancel delivery is still in flight.
        with database_sessions() as session:
            stage_dispatch(session, start_request(workflow_id))
            session.commit()
        # Publisher B decides the start before the cancel RPC is released.
        start_task = asyncio.create_task(
            publisher(async_database_sessions, client).publish_pending()
        )
        delivered_start = await start_task
        # Assert before releasing the cancel RPC: the start must already be
        # blocked by the durable cancel intent while the cancel is unconfirmed.
        with Session(database_engine) as session:
            cancel_row = session.scalar(
                select(TemporalOutboxEntry).where(
                    TemporalOutboxEntry.workflow_id == workflow_id,
                    TemporalOutboxEntry.operation == DispatchOperation.CANCEL.value,
                )
            )
        assert cancel_row is not None
        assert cancel_row.decided_at is not None, "the cancel intent must be durable"
        assert cancel_row.published_at is None, "the cancel must still be unconfirmed"
        assert client.calls == [], "the start must be blocked before the cancel is confirmed"
        client.release.set()
        return await cancel_task, delivered_start

    delivered_cancel, delivered_start = asyncio.run(run_interleaved())
    assert delivered_cancel == 1, "the cancel completes against the absent workflow"
    assert delivered_start == 1, "the late start is recorded as superseded"
    assert client.calls == [], "a workflow with a cancel intent must never be started"
    assert client.running == set()


def test_signal_delivery_carries_the_stable_dispatch_identity(
    database_sessions, async_database_sessions, database_engine
) -> None:
    workflow_id = "signal-identity-run"
    with database_sessions() as session:
        stage_dispatch(session, start_request(workflow_id))
        session.commit()

    client = RecordingClient()
    assert asyncio.run(publisher(async_database_sessions, client).publish_pending()) == 1
    assert workflow_id in client.running

    request = signal_request(workflow_id)
    with database_sessions() as session:
        stage_signal(
            session,
            dispatch_id=request.dispatch_id,
            workflow_id=workflow_id,
            signal_name="resume",
            signal_args=request.signal_args,
        )
        session.commit()

    assert asyncio.run(publisher(async_database_sessions, client).publish_pending()) == 1
    assert len(client.signal_calls) == 1
    signal_name, signal_args = client.signal_calls[0]
    assert signal_name == "resume"
    assert signal_args[0] == str(request.dispatch_id), (
        "the dispatch identity leads the payload for receiver-side deduplication"
    )
    assert signal_args[1:] == ({"approved": True},)


def load_cancel_row(engine: Engine, workflow_id: str) -> TemporalOutboxEntry | None:
    with Session(engine) as session:
        return session.scalar(
            select(TemporalOutboxEntry).where(
                TemporalOutboxEntry.workflow_id == workflow_id,
                TemporalOutboxEntry.operation == DispatchOperation.CANCEL.value,
            )
        )


def set_available_at(engine: Engine, operation: DispatchOperation, when: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(TemporalOutboxEntry)
            .where(TemporalOutboxEntry.operation == operation.value)
            .values(available_at=when)
        )


def postpone_rows(engine: Engine, operation: DispatchOperation, delay: timedelta) -> None:
    set_available_at(engine, operation, datetime.now(UTC) + delay)


def release_rows(engine: Engine, operation: DispatchOperation) -> None:
    set_available_at(engine, operation, datetime.now(UTC))


def expire_claims(engine: Engine) -> None:
    """Simulate expired claim leases, as after a publisher process exit."""
    with engine.begin() as connection:
        connection.execute(
            update(TemporalOutboxEntry).values(claim_token=None, claim_until=None)
        )
