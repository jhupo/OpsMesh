"""Exercise transaction, duplicate delivery and claim recovery with real PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from opentelemetry import propagate
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.contrib.opentelemetry import create_tracer_provider

from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.runtime.temporal.outbox import TemporalOutboxPublisher, stage_execution
from opsmesh.shared.db.base import Base
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.temporal import seed_workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


@pytest.mark.parametrize("ratio", [0, 1])
def test_outbox_preserves_native_sampling_decision_after_commit(ratio) -> None:
    provider = create_tracer_provider(sampler=ParentBased(TraceIdRatioBased(ratio)))
    try:
        with temporary_postgres_schema() as engine:
            Base.metadata.create_all(engine)
            with sessionmaker(engine, expire_on_commit=False)() as session:
                _, workspace = seed_workspace(session)
                reference = ExecutionRequest(
                    workspace_id=workspace.id,
                    resource_id=uuid4(),
                    kind=ExecutionKind.AUDIT_INTEGRITY,
                )
                tracer = provider.get_tracer("outbox-sampling-acceptance")
                with tracer.start_as_current_span("submission") as span:
                    assert span.is_recording() is bool(ratio)
                    stage_execution(session, reference)
                session.commit()
                row = session.get(TemporalOutboxEntry, reference.execution_id)
                carrier = {"traceparent": row.payload["traceparent"]}
                assert carrier["traceparent"].endswith("-01" if ratio else "-00")
                with tracer.start_as_current_span(
                    "delivery", context=propagate.extract(carrier)
                ) as downstream:
                    assert downstream.is_recording() is bool(ratio)
                    assert (
                        downstream.get_span_context().trace_id == span.get_span_context().trace_id
                    )
    finally:
        provider.shutdown()


def test_outbox_is_atomic_idempotent_and_claimed_once_across_publishers() -> None:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            _, workspace = seed_workspace(session)
            reference = ExecutionRequest(
                workspace_id=workspace.id, resource_id=uuid4(), kind=ExecutionKind.AUDIT_INTEGRITY
            )
            stage_execution(session, reference)
            session.rollback()
            assert session.scalar(select(TemporalOutboxEntry.id)) is None
            provider = create_tracer_provider()
            with provider.get_tracer(__name__).start_as_current_span("submission"):
                submitted = {}
                propagate.inject(submitted)
                stage_execution(session, reference)
                with provider.get_tracer(__name__).start_as_current_span("duplicate"):
                    stage_execution(session, reference)
            provider.shutdown()
            session.commit()
            assert len(list(session.scalars(select(TemporalOutboxEntry)))) == 1
            assert (
                session.get(TemporalOutboxEntry, reference.execution_id).payload["traceparent"]
                == submitted["traceparent"]
            )
            with pytest.raises(ValueError, match="different operation"):
                stage_execution(session, reference.model_copy(update={"resource_id": uuid4()}))
            session.rollback()

        async def verify() -> None:
            # Reuse the schema option from the actual connection, without a second fact store.
            with engine.connect() as connection:
                search_path = connection.exec_driver_sql("SHOW search_path").scalar_one()
            async_engine = create_async_engine(
                make_url(engine.url).set(drivername="postgresql+psycopg"),
                connect_args={"options": f"-csearch_path={search_path}"},
            )
            sessions = async_sessionmaker(async_engine, expire_on_commit=False)

            class Transport:
                def __init__(self):
                    self.ids = []

                async def dispatch(self, request):
                    assert request.traceparent == submitted["traceparent"]
                    self.ids.append(request.dispatch_id)
                    await asyncio.sleep(0.05)

            transport = Transport()
            try:
                publishers = [TemporalOutboxPublisher(sessions, transport) for _ in range(2)]
                results = await asyncio.gather(*(item.publish_pending() for item in publishers))
                assert sum(results) == 1
                assert transport.ids == [reference.execution_id]
                async with sessions() as session:
                    row = await session.get(TemporalOutboxEntry, reference.execution_id)
                    assert row.published_at is not None
                    assert row.claim_token is None
            finally:
                await async_engine.dispose()

        asyncio.run(verify())


def test_claim_deadline_prevents_hot_loop_and_expired_claim_is_recovered() -> None:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            _, workspace = seed_workspace(session)
            request = ExecutionRequest(
                workspace_id=workspace.id, resource_id=uuid4(), kind=ExecutionKind.AUDIT_INTEGRITY
            )
            stage_execution(session, request)
            row = session.get(TemporalOutboxEntry, request.execution_id)
            deadline = datetime.now(UTC) + timedelta(seconds=30)
            row.claim_token = uuid4()
            row.claim_until = deadline
            session.commit()
        with engine.connect() as connection:
            search_path = connection.exec_driver_sql("SHOW search_path").scalar_one()

        async def verify() -> None:
            async_engine = create_async_engine(
                make_url(engine.url).set(drivername="postgresql+psycopg"),
                connect_args={"options": f"-csearch_path={search_path}"},
            )
            sessions = async_sessionmaker(async_engine, expire_on_commit=False)

            class Transport:
                async def dispatch(self, request):
                    return None

            publisher = TemporalOutboxPublisher(sessions, Transport())
            try:
                assert await publisher.publish_pending() == 0
                assert await publisher.next_delivery_at() == deadline
                async with sessions.begin() as session:
                    row = await session.get(TemporalOutboxEntry, request.execution_id)
                    row.claim_until = datetime.now(UTC) - timedelta(seconds=1)
                assert await publisher.publish_pending() == 1
            finally:
                await async_engine.dispose()

        asyncio.run(verify())


def test_publisher_bounds_network_tasks_and_preserves_new_claim_owner() -> None:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            _, workspace = seed_workspace(session)
            references = [
                ExecutionRequest(
                    workspace_id=workspace.id,
                    resource_id=uuid4(),
                    kind=ExecutionKind.AUDIT_INTEGRITY,
                )
                for _ in range(6)
            ]
            for reference in references:
                stage_execution(session, reference)
            session.commit()
        with engine.connect() as connection:
            search_path = connection.exec_driver_sql("SHOW search_path").scalar_one()

        async def verify():
            async_engine = create_async_engine(
                make_url(engine.url).set(drivername="postgresql+psycopg"),
                connect_args={"options": f"-csearch_path={search_path}"},
            )
            sessions = async_sessionmaker(async_engine, expire_on_commit=False)

            class Transport:
                def __init__(self):
                    self.active = self.peak = 0

                async def dispatch(self, request):
                    self.active += 1
                    self.peak = max(self.active, self.peak)
                    await asyncio.sleep(0.02)
                    self.active -= 1

            transport = Transport()
            publisher = TemporalOutboxPublisher(sessions, transport, concurrency=2)
            try:
                assert await publisher.publish_pending(limit=6) == 6
                assert transport.peak == 2
                new_owner = uuid4()
                async with sessions.begin() as session:
                    row = await session.get(TemporalOutboxEntry, references[0].execution_id)
                    row.published_at = None
                    row.claim_token = new_owner
                    row.claim_until = datetime.now(UTC) + timedelta(seconds=60)
                await publisher._record_published(references[0].execution_id, uuid4())
                async with sessions() as session:
                    row = await session.get(TemporalOutboxEntry, references[0].execution_id)
                    assert row.published_at is None
                    assert row.claim_token == new_owner
            finally:
                await async_engine.dispose()

        asyncio.run(verify())
