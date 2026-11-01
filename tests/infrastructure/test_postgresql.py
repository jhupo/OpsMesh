"""Explicit real PostgreSQL acceptance; never connect to or modify an existing server."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import event, inspect, select, text, update

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.agents import (
    AgentBackendCapabilities,
    AgentRunCompleted,
    AgentRunInterrupted,
    AgentSpecification,
    PendingToolApproval,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.coordination.admission import AtomicRunAdmission
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.migrations import SCHEMA_TABLE
from opsmesh.modules.runtime.api import SubmitRunRequest
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from opsmesh.modules.runtime.internal.service import PersistentAgentRuntime
from opsmesh.modules.sessions.internal.service import ConversationService
from tests.infrastructure.postgresql_service import TemporaryPostgreSQL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPSMESH_TEST_POSTGRES_INTEGRATION") != "1",
        reason="Explicit temporary PostgreSQL integration",
    ),
]


@pytest.fixture(scope="module")
def postgres():
    configured = os.getenv("OPSMESH_TEST_POSTGRES_BIN")
    if not configured:
        pytest.fail("OPSMESH_TEST_POSTGRES_BIN must point to PostgreSQL binaries")
    with TemporaryPostgreSQL(Path(configured).resolve()) as service:
        yield service


@pytest.fixture
async def postgres_url(postgres):
    name = "opsmesh_" + uuid4().hex
    admin = await asyncpg.connect(postgres.url(async_driver=False))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        yield postgres.url(name)
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_postgres_migration_is_serialized_and_verify_is_read_only(postgres_url):
    databases = [AsyncDatabase(postgres_url), AsyncDatabase(postgres_url)]
    stores = [VersionedRecordStore(db, "things") for db in databases]
    try:
        with pytest.raises(OpsMeshError) as error:
            await databases[0].verify_schema()
        assert error.value.code == "SCHEMA_NOT_INITIALIZED"
        await asyncio.gather(*(db.initialize() for db in databases))
        value = await stores[0].create("one", {"unicode": "真实数据库", "nested": {"value": 1}})
        assert value.created_at.tzinfo is not None
        statements = []
        event.listen(
            databases[1].engine.sync_engine,
            "before_cursor_execute",
            lambda *args: statements.append(args[2].lstrip().lower()),
        )
        async with databases[1].engine.connect() as connection:
            await connection.execute(text("SET default_transaction_read_only = on"))
            await connection.commit()
        await databases[1].verify_schema()
        assert (await stores[1].get("one")).data["unicode"] == "真实数据库"
        assert not any(
            sql.startswith(("create", "insert", "update", "alter")) for sql in statements
        )
        async with databases[0].engine.connect() as connection:
            version = (await connection.execute(text("SELECT version()"))).scalar_one()
            assert version.startswith("PostgreSQL")
            names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert set(names) == {"things", "opsmesh_outbox", SCHEMA_TABLE}
    finally:
        await asyncio.gather(*(db.close() for db in databases))


async def test_postgres_run_projection_upgrade_preserves_existing_rows(postgres_url):
    from tests.runtime.test_indexed_records import assert_record_upgrade

    await assert_record_upgrade(postgres_url)


async def test_postgres_terminal_session_recovery_across_instances(postgres_url):
    databases = [AsyncDatabase(postgres_url), AsyncDatabase(postgres_url)]
    sessions = [ConversationService(database) for database in databases]
    extensions = RuntimeExtensionRegistry()

    class Backend:
        id, version = "controlled", "1"
        features = AgentBackendCapabilities()

    extensions.register_backend("controlled", lambda: Backend())

    async def unused(view):
        raise AssertionError("Recovery must not execute a backend")

    workers = [
        PersistentAgentRuntime(
            database,
            extensions,
            unused,
            session_claims=claims,
        )
        for database, claims in zip(databases, sessions, strict=True)
    ]
    try:
        await databases[0].initialize()
        await databases[1].verify_schema()
        session = await sessions[0].open("owner", "agent@1")
        await sessions[0].open("owner", "idle@1")
        submission = SubmitRunRequest(
            principal_id="owner",
            session_id=session.id,
            agent=AgentSpecification(id="agent", name="Agent", backend="controlled"),
            input="hello",
            idempotency_key="first",
        )
        run = await workers[0].submit(submission)
        await workers[0].cancel(run.id)
        # Reconstruct a late claim after cancellation, as left by an interrupted worker.
        await sessions[0].claim(session.id, run.id)
        assert await sessions[1].active_claims() == {session.id: run.id}
        await asyncio.gather(*(worker.recover_stale() for worker in workers))
        assert await sessions[0].active_claims() == {}
        replacement = await workers[1].submit(
            submission.model_copy(update={"idempotency_key": "second"})
        )
        await sessions[1].claim(session.id, replacement.id)
        await asyncio.gather(*(worker.reconcile_session(run.id) for worker in workers))
        assert await sessions[0].active_claims() == {session.id: replacement.id}
    finally:
        await asyncio.gather(*(database.close() for database in databases))


@pytest.mark.parametrize("mutation", ["column", "index", "fingerprint"])
async def test_postgres_schema_drift_never_auto_repairs(postgres_url, mutation):
    db = AsyncDatabase(postgres_url)
    store = VersionedRecordStore(db, "things")
    try:
        await db.initialize()
        statements = {
            "column": "ALTER TABLE things ADD COLUMN unexpected text",
            "index": "DROP INDEX ix_things_created",
            "fingerprint": (
                f"UPDATE {SCHEMA_TABLE} SET fingerprint = 'invalid' WHERE table_name = 'things'"
            ),
        }
        async with db.engine.begin() as connection:
            await connection.execute(text(statements[mutation]))
        for operation in (db.verify_schema, db.initialize):
            with pytest.raises(OpsMeshError) as error:
                await operation()
            assert error.value.code in {"SCHEMA_DRIFT", "SCHEMA_VERSION_MISMATCH"}
        with pytest.raises(OpsMeshError) as error:
            await store.list()
        assert error.value.code == "SCHEMA_NOT_INITIALIZED"
    finally:
        await db.close()


async def test_postgres_concurrent_cas_and_outbox_rollback(postgres_url):
    databases = [AsyncDatabase(postgres_url), AsyncDatabase(postgres_url)]
    stores = [VersionedRecordStore(db, "things") for db in databases]
    try:
        await databases[0].initialize()
        await databases[1].verify_schema()
        await stores[0].create("one", {"value": 0})
        results = await asyncio.gather(
            *(
                store.update(
                    "one",
                    {"value": index},
                    1,
                    events=({"id": f"changed-{index}", "type": "thing.changed"},),
                )
                for index, store in enumerate(stores, 1)
            ),
            return_exceptions=True,
        )
        assert (
            sum(
                isinstance(result, OpsMeshError) and result.code == "CONFLICT" for result in results
            )
            == 1
        )
        final = await stores[0].get("one")
        events = await databases[0].pending_events()
        assert final.revision == 2 and len(events) == 1
        duplicate = {"id": events[0]["id"], "type": "duplicate"}
        with pytest.raises(OpsMeshError) as error:
            await stores[1].update("one", {"value": "must roll back"}, 2, events=(duplicate,))
        assert error.value.code == "CONFLICT"
        assert await stores[0].get("one") == final
        with pytest.raises(OpsMeshError):
            await stores[1].create("must-not-exist", {}, events=(duplicate,))
        assert await stores[0].get("must-not-exist") is None
    finally:
        await asyncio.gather(*(db.close() for db in databases))


async def test_postgres_skip_locked_claims_and_expired_owner_fencing(postgres_url):
    databases = [AsyncDatabase(postgres_url), AsyncDatabase(postgres_url)]
    stores = [VersionedRecordStore(db, "things") for db in databases]
    try:
        await databases[0].initialize()
        await databases[1].verify_schema()
        await stores[0].create(
            "one",
            {},
            events=tuple({"id": f"event-{index:02}", "type": "created"} for index in range(20)),
        )
        # Hold the first ten rows from a separate transaction: claim must skip, not wait.
        async with databases[0].engine.begin() as lock:
            locked = (
                (
                    await lock.execute(
                        select(databases[0].outbox.c.id)
                        .order_by(databases[0].outbox.c.id)
                        .limit(10)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            batch = await asyncio.wait_for(databases[1].claim_events("new-owner", limit=10), 3)
            assert len(batch) == 10 and not set(locked) & {item["id"] for item in batch}
        old = await databases[0].claim_events("old-owner", limit=10)
        assert len(old) == 10
        async with databases[0].engine.begin() as connection:
            await connection.execute(
                update(databases[0].outbox)
                .where(databases[0].outbox.c.lease_owner == "old-owner")
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        replacement = await databases[1].claim_events("replacement", limit=10)
        assert {item["id"] for item in replacement} == {item["id"] for item in old}
        for operation in (
            databases[0].acknowledge_event,
            databases[0].renew_event_lease,
            databases[0].reject_event,
        ):
            with pytest.raises(OpsMeshError) as error:
                await operation(old[0]["id"], "old-owner")
            assert error.value.code == "OUTBOX_LEASE_LOST"
        await databases[1].renew_event_lease(replacement[0]["id"], "replacement")
        await databases[1].acknowledge_event(replacement[0]["id"], "replacement")
    finally:
        await asyncio.gather(*(db.close() for db in databases))


async def test_postgres_multiworker_capacity_and_expired_token(postgres_url):
    databases = [AsyncDatabase(postgres_url), AsyncDatabase(postgres_url)]
    pools = [AtomicRunAdmission(db) for db in databases]
    try:
        await databases[0].initialize()
        await databases[1].verify_schema()
        results = await asyncio.gather(
            *(pools[index % 2].acquire(f"worker-{index}", 3) for index in range(12)),
            return_exceptions=True,
        )
        admitted = [
            (index, result) for index, result in enumerate(results) if isinstance(result, str)
        ]
        assert len(admitted) == 3
        assert all(
            isinstance(result, str)
            or (isinstance(result, OpsMeshError) and result.code == "CAPACITY_BUSY")
            for result in results
        )
        for index, token in admitted:
            await pools[(index + 1) % 2].renew(f"worker-{index}", token)
            await pools[(index + 1) % 2].release(f"worker-{index}", token)
        old = await pools[0].acquire("same-owner", 1, lease_seconds=0.15)
        await asyncio.sleep(0.2)
        replacement = await pools[1].acquire("same-owner", 1)
        assert old != replacement
        for operation in (pools[0].renew, pools[0].release):
            with pytest.raises(OpsMeshError) as error:
                await operation("same-owner", old)
            assert error.value.code == "LEASE_LOST"
        await pools[1].release("same-owner", replacement)
    finally:
        await asyncio.gather(*(db.close() for db in databases))


async def test_postgres_application_restart_preserves_approval_and_session_claim(
    postgres_url, tmp_path
):
    calls = []

    class ApprovalBackend:
        id = "postgres_approval"
        version = "1"
        features = AgentBackendCapabilities(approvals=True, resume=True)

        async def run(self, spec, input, context, *, checkpoint=None, decisions=None, **options):
            calls.append((checkpoint, decisions))
            if checkpoint is None:
                return AgentRunInterrupted(
                    pending=(
                        PendingToolApproval(
                            id="write-report", tool_name="report", agent_name=spec.name
                        ),
                    ),
                    checkpoint={"report": "persisted before shutdown"},
                )
            assert checkpoint == {"report": "persisted before shutdown"}
            assert decisions == {"write-report": True}
            return AgentRunCompleted(
                output="approved after restart",
                history=({"role": "assistant", "content": "approved after restart"},),
            )

    settings = OpsMeshSettings(
        database_url=postgres_url, data_directory=tmp_path, worker_enabled=False
    )
    first = OpsMeshApplication(settings)
    first.services.extensions.register_backend(ApprovalBackend.id, ApprovalBackend)
    try:
        await first.initialize(admin_username="admin", admin_password="test-passphrase")
        await first.start()
        user = (await first.services.accounts.list_users())[0]
        draft = await first.services.agents.create(
            AgentSpecification(id="reporter", name="Reporter", backend=ApprovalBackend.id), user.id
        )
        await first.services.agents.publish("reporter", draft.revision)
        session = await first.services.sessions.open(user.id, "reporter@1")
        run = await first.submit_run(
            principal_id=user.id,
            session_id=session.id,
            agent_ref="reporter@1",
            input="create report",
            idempotency_key="restart-approval",
        )
        waiting = await first.services.runtime.execute(run.id)
        assert waiting.status == "waiting_approval", waiting.error
        assert (await first.services.sessions.get(session.id)).active_run_id == run.id
    finally:
        await first.close()

    # All services and connection pools are new; only PostgreSQL and artifact files survive.
    second = OpsMeshApplication(settings)
    second.services.extensions.register_backend(ApprovalBackend.id, ApprovalBackend)
    try:
        await second.start()  # Read-only schema verification, never a second initialization.
        restored = await second.services.runtime.get_run(run.id)
        assert restored.status == "waiting_approval" and restored.has_checkpoint
        assert restored.revision == waiting.revision
        assert (await second.services.sessions.get(session.id)).active_run_id == run.id
        decided = await second.services.runtime.decide(
            run.id, "write-report", True, restored.revision
        )
        await second.services.runtime.resume(run.id, decided.revision)
        completed = await second.services.runtime.execute(run.id)
        assert completed.status == "completed", completed.error
        assert completed.output == "approved after restart" and not completed.has_checkpoint
        assert (await second.services.sessions.get(session.id)).active_run_id is None
        assert [item.role for item in await second.services.sessions.history(session.id)] == [
            "user",
            "assistant",
        ]
        replay = await second.submit_run(
            principal_id=user.id,
            session_id=session.id,
            agent_ref="reporter@1",
            input="create report",
            idempotency_key="restart-approval",
        )
        assert replay.id == run.id and replay.status == "completed"
        assert len(calls) == 2
        facts = await second.services.database.pending_events()
        assert sum(fact["type"] == "runtime.run.completed" for fact in facts) == 1
    finally:
        await second.close()
