import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, inspect, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.migrations import SCHEMA_TABLE


@contextmanager
def expect_error(code: str) -> Iterator[None]:
    with pytest.raises(OpsMeshError) as captured:
        yield
    assert captured.value.code.startswith(code)


def database_at(path: Path) -> AsyncDatabase:
    return AsyncDatabase(f"sqlite:///{path.as_posix()}")


async def table_names(database: AsyncDatabase) -> list[str]:
    async with database.engine.connect() as connection:
        return await connection.run_sync(lambda sync: inspect(sync).get_table_names())


async def test_verification_does_not_create_schema(tmp_path: Path) -> None:
    database = database_at(tmp_path / "empty.db")
    store = VersionedRecordStore(database, "things")
    try:
        with expect_error("SCHEMA_NOT_INITIALIZED"):
            await database.verify_schema()
        assert await table_names(database) == []
        with expect_error("SCHEMA_NOT_INITIALIZED"):
            await store.create("one", {})
    finally:
        await database.close()


async def test_initial_migration_reopen_and_readonly_verification(tmp_path: Path) -> None:
    path = tmp_path / "schema.db"
    database = database_at(path)
    store = VersionedRecordStore(database, "things")
    await database.initialize()
    await store.create("one", {"value": 1})
    await database.initialize()
    await database.close()

    reopened = database_at(path)
    reopened_store = VersionedRecordStore(reopened, "things")
    statements: list[str] = []

    def capture(*args: Any) -> None:
        statements.append(args[2].lstrip().lower())

    event.listen(reopened.engine.sync_engine, "before_cursor_execute", capture)
    try:
        await reopened.verify_schema()
        assert all(
            not sql.startswith(("create", "insert", "update", "alter")) for sql in statements
        )
        record = await reopened_store.get("one")
        assert record is not None and record.data == {"value": 1}
        assert record.created_at.tzinfo is not None
        assert set(await table_names(reopened)) == {"things", "opsmesh_outbox", SCHEMA_TABLE}
    finally:
        await reopened.close()


async def test_new_module_requires_explicit_migration(tmp_path: Path) -> None:
    database = database_at(tmp_path / "addition.db")
    await database.initialize()
    store = VersionedRecordStore(database, "added_later")
    try:
        with expect_error("SCHEMA_NOT_INITIALIZED"):
            await store.list()
        with expect_error("SCHEMA_NOT_INITIALIZED"):
            await database.verify_schema()
        await database.initialize()
        await store.create("ready", {})
    finally:
        await database.close()


async def test_unversioned_existing_table_is_not_silently_adopted(tmp_path: Path) -> None:
    database = database_at(tmp_path / "legacy.db")
    store = VersionedRecordStore(database, "things")
    async with database.engine.begin() as connection:
        await connection.run_sync(store.table.create)
    try:
        with expect_error("SCHEMA_UNVERSIONED"):
            await database.initialize()
        assert await table_names(database) == ["things"]
    finally:
        await database.close()


@pytest.mark.parametrize("mutation", ["version", "fingerprint", "column", "index", "missing"])
async def test_schema_version_and_structure_drift_are_rejected(
    tmp_path: Path, mutation: str
) -> None:
    database = database_at(tmp_path / "drift.db")
    store = VersionedRecordStore(database, "things")
    await database.initialize()
    commands = {
        "version": f"UPDATE {SCHEMA_TABLE} SET version = 99 WHERE table_name = 'things'",
        "fingerprint": f"UPDATE {SCHEMA_TABLE} SET fingerprint = 'bad' WHERE table_name = 'things'",
        "column": "ALTER TABLE things ADD COLUMN unexpected TEXT",
        "index": "DROP INDEX ix_things_created",
        "missing": "DROP TABLE things",
    }
    async with database.engine.begin() as connection:
        await connection.execute(text(commands[mutation]))
    try:
        with expect_error("SCHEMA_"):
            await database.verify_schema()
        with expect_error("SCHEMA_NOT_INITIALIZED"):
            await store.list()
        with expect_error("SCHEMA_"):
            await database.initialize()
    finally:
        await database.close()


async def test_migration_catalog_structure_drift_has_a_framework_error(tmp_path: Path) -> None:
    database = database_at(tmp_path / "catalog-drift.db")
    await database.initialize()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(f"ALTER TABLE {SCHEMA_TABLE} RENAME COLUMN version TO changed")
        )
    try:
        with expect_error("SCHEMA_DRIFT"):
            await database.verify_schema()
        with expect_error("SCHEMA_DRIFT"):
            await database.initialize()
    finally:
        await database.close()


async def test_record_and_events_are_one_transaction(tmp_path: Path) -> None:
    database = database_at(tmp_path / "atomic.db")
    store = VersionedRecordStore(database, "things")
    await database.initialize()
    canonical_event = {
        "id": "event-one",
        "type": "thing.created",
        "source": "things",
        "payload": {"value": "canonical"},
        "subject": {"id": "one", "value": "legacy"},
        "correlation_id": "run-one",
        "custom_extension": {"preserved": True},
    }
    try:
        first = await store.create("one", {"value": 1}, events=(canonical_event,))
        queued = (await database.pending_events())[0]
        assert queued["payload"] == {"id": "one", "value": "canonical"}
        assert queued["subject"] == canonical_event["subject"]
        assert queued["custom_extension"] == {"preserved": True}
        assert queued["correlation_id"] == "run-one" and queued["schema_version"] == 1
        assert datetime.fromisoformat(queued["occurred_at"]).tzinfo is not None
        with expect_error("CONFLICT"):
            await store.create("two", {}, events=(canonical_event,))
        assert await store.get("two") is None
        with expect_error("CONFLICT"):
            await store.update("one", {"value": 2}, first.revision, events=(canonical_event,))
        unchanged = await store.get("one")
        assert unchanged == first
        with expect_error("EVENT_INVALID"):
            await store.delete("one", first.revision, events=({"payload": {}},))
        assert await store.get("one") == first
        await store.delete("one", first.revision, events=({"type": "thing.deleted"},))
        assert await store.get("one") is None
        assert len(await database.pending_events()) == 2
    finally:
        await database.close()


async def test_cas_prevents_lost_updates_across_database_instances(tmp_path: Path) -> None:
    path = tmp_path / "cas.db"
    databases = [database_at(path), database_at(path)]
    stores = [VersionedRecordStore(database, "things") for database in databases]
    await databases[0].initialize()
    await databases[1].verify_schema()
    await stores[0].create("one", {"value": 1})
    try:
        results = await asyncio.gather(
            stores[0].update("one", {"value": 2}, 1),
            stores[1].update("one", {"value": 3}, 1),
            return_exceptions=True,
        )
        assert sum(isinstance(result, OpsMeshError) for result in results) == 1
        final = await stores[0].get("one")
        assert final is not None and final.revision == 2
    finally:
        await asyncio.gather(*(database.close() for database in databases))


async def test_multiworker_claims_are_disjoint(tmp_path: Path) -> None:
    path = tmp_path / "workers.db"
    databases = [database_at(path), database_at(path)]
    stores = [VersionedRecordStore(database, "things") for database in databases]
    await databases[0].initialize()
    await databases[1].verify_schema()
    await stores[0].create(
        "one", {}, events=tuple({"id": f"event-{i}", "type": "thing.created"} for i in range(20))
    )
    try:
        first, second = await asyncio.gather(
            databases[0].claim_events("worker-a", limit=10),
            databases[1].claim_events("worker-b", limit=10),
        )
        assert len(first) == len(second) == 10
        assert not {item["id"] for item in first}.intersection(item["id"] for item in second)
        assert await databases[0].pending_events() == []
        with expect_error("OUTBOX_LEASE_LOST"):
            await databases[0].acknowledge_event(first[0]["id"], "worker-b")
        with expect_error("OUTBOX_LEASE_LOST"):
            await databases[0].acknowledge_event(first[0]["id"])
        for database, owner, batch in zip(
            databases, ("worker-a", "worker-b"), (first, second), strict=True
        ):
            for item in batch:
                await database.acknowledge_event(item["id"], owner)
        await databases[0].acknowledge_event(first[0]["id"], "worker-a")
        assert await databases[1].claim_events("worker-c") == []
    finally:
        await asyncio.gather(*(database.close() for database in databases))


async def test_retry_backoff_deadletter_and_requeue(tmp_path: Path) -> None:
    database = database_at(tmp_path / "retry.db")
    store = VersionedRecordStore(database, "things")
    await database.initialize()
    await store.create("one", {}, events=({"id": "retry", "type": "thing.created"},))
    try:
        await database.claim_events("first")
        await database.reject_event("retry", "first", error="temporary", retry_base_seconds=2)
        assert await database.claim_events("second") == []
        async with database.engine.begin() as connection:
            row = (await connection.execute(select(database.outbox))).one()
            assert row.attempts == 1 and row.last_error == "temporary"
            await connection.execute(
                update(database.outbox).values(
                    available_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            )
        assert len(await database.claim_events("second")) == 1
        await database.reject_event("retry", "second", max_attempts=2, error="permanent")
        dead = await database.dead_letters()
        assert len(dead) == 1 and dead[0]["attempts"] == 2 and dead[0]["last_error"] == "permanent"
        assert await database.claim_events("third") == []
        await database.requeue_event("retry")
        assert await database.dead_letters() == []
        assert len(await database.claim_events("third")) == 1
        await database.acknowledge_event("retry", "third")
    finally:
        await database.close()


async def test_expired_lease_can_be_reclaimed_and_crashes_are_bounded(tmp_path: Path) -> None:
    database = database_at(tmp_path / "leases.db")
    store = VersionedRecordStore(database, "things")
    await database.initialize()
    await store.create("one", {}, events=({"id": "leased", "type": "thing.created"},))
    try:
        await database.claim_events("first", max_attempts=2)
        await database.renew_event_lease("leased", "first", lease_seconds=60)
        for owner in ("second", "third"):
            async with database.engine.begin() as connection:
                await connection.execute(
                    update(database.outbox).values(
                        lease_until=datetime.now(UTC) - timedelta(seconds=1)
                    )
                )
            with expect_error("OUTBOX_LEASE_LOST"):
                await database.acknowledge_event("leased", "first")
            batch = await database.claim_events(owner, max_attempts=2)
            assert len(batch) == (1 if owner == "second" else 0)
        assert len(await database.dead_letters()) == 1
    finally:
        await database.close()


async def test_in_memory_database_serializes_shared_connection_transactions() -> None:
    database = AsyncDatabase("sqlite:///:memory:")
    store = VersionedRecordStore(database, "things")
    await database.initialize()
    try:
        await asyncio.gather(*(store.create(str(i), {"n": i}) for i in range(12)))
        assert len(await store.list()) == 12
        await asyncio.gather(database.claim_events("one"), database.claim_events("two"))
    finally:
        await database.close()


@pytest.mark.parametrize("data", [{"bad": float("nan")}, {"nested": {1: "bad"}}, {"bad": object()}])
async def test_nonportable_record_values_are_rejected(tmp_path: Path, data: dict[str, Any]) -> None:
    database = database_at(tmp_path / "invalid.db")
    store = VersionedRecordStore(database, "things")
    await database.initialize()
    try:
        with expect_error("RECORD_INVALID"):
            await store.create("one", data)
        assert await store.list() == []
    finally:
        await database.close()


@pytest.mark.parametrize(
    "url",
    ["postgres://user:secret@localhost/opsmesh", "postgresql://user:secret@localhost/opsmesh"],
)
async def test_postgresql_urls_and_registered_schema_compile_without_connecting(url: str) -> None:
    database = AsyncDatabase(url)
    VersionedRecordStore(database, "things")
    try:
        assert database.engine.url.drivername == "postgresql+asyncpg"
        sql = "\n".join(
            str(CreateTable(table).compile(dialect=postgresql.dialect()))
            for table in database.metadata.sorted_tables
        )
        assert "JSON NOT NULL" in sql and "TIMESTAMP WITH TIME ZONE" in sql
        assert "CHECK (revision >= 1)" in sql
        for table in database.metadata.tables.values():
            for index in table.indexes:
                assert "CREATE INDEX" in str(
                    CreateIndex(index).compile(dialect=postgresql.dialect())
                )
    finally:
        await database.close()
