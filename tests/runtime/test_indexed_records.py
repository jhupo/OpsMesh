"""Indexed queries preserve durable records and migrate existing installations explicitly."""

import pytest
from sqlalchemy import event, inspect, select, text

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.runtime.internal.records import RunRecordStore, RunStreamStore


async def test_v1_record_upgrade_backfills_indexes_without_changing_records(tmp_path):
    url = f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    await assert_record_upgrade(url)


async def assert_record_upgrade(url):
    previous = AsyncDatabase(url)
    runs = VersionedRecordStore(previous, "runtime_runs")
    streams = VersionedRecordStore(previous, "runtime_stream")
    await previous.initialize()
    original = await runs.create(
        "old-run",
        {
            "status": "running",
            "principal_id": "alice",
            "session_id": "group",
            "lease_expires": 12.5,
        },
    )
    original_stream = await streams.create("old-event", {"run_id": "old-run", "seq": 17})
    await previous.close()

    database = AsyncDatabase(url)
    runs, streams = RunRecordStore(database), RunStreamStore(database)
    try:
        with pytest.raises(OpsMeshError, match=r"migration|version|schema"):
            await database.verify_schema()
        await database.initialize()
        await database.verify_schema()
        assert await runs.get("old-run") == original
        assert await streams.get("old-event") == original_stream
        assert await runs.select_runs(
            statuses=("running",), principal_id="alice", session_id="group"
        ) == [original]
        assert await runs.select_runs(expired_before=13) == [original]
        assert await runs.select_runs(expired_before=12) == []
        assert await streams.read("old-run", 16) == [original_stream]
        assert await streams.read("old-run", 17) == []
        async with database.engine.connect() as connection:
            versions = dict(
                (
                    await connection.execute(
                        text("SELECT table_name, version FROM opsmesh_schema_versions")
                    )
                ).all()
            )
            indexes = await connection.run_sync(
                lambda sync: inspect(sync).get_indexes("runtime_stream")
            )
        assert versions["runtime_runs"] == versions["runtime_stream"] == 2
        assert any(index["column_names"] == ["run_id", "seq"] for index in indexes)
        await database.initialize()
        assert await runs.get("old-run") == original
    finally:
        await database.close()


async def test_projection_updates_are_atomic_and_keyset_survives_status_changes(tmp_path):
    database = AsyncDatabase(f"sqlite:///{(tmp_path / 'queries.db').as_posix()}")
    runs = RunRecordStore(database)
    await database.initialize()
    try:
        for index in range(7):
            await runs.create(
                f"run-{index}",
                {
                    "status": "queued",
                    "principal_id": "alice" if index < 6 else "bob",
                    "session_id": "group",
                },
            )
        cursor = None
        visited = []
        while page := await runs.select_runs(
            statuses=("queued",), principal_id="alice", limit=2, after_id=cursor
        ):
            cursor = page[-1].id
            for record in page:
                visited.append(record.id)
                await runs.update(
                    record.id, {**record.data, "status": "completed"}, record.revision
                )
        assert visited == [f"run-{index}" for index in range(6)]
        assert await runs.count_statuses(("completed",)) == 6
        record = await runs.require("run-6")
        with pytest.raises(OpsMeshError):
            await runs.update(record.id, {**record.data, "status": "failed"}, record.revision + 1)
        assert await runs.count_statuses(("queued",)) == 1
        async with database.engine.connect() as connection:
            projected = await connection.scalar(
                select(runs.table.c.status).where(runs.table.c.id == record.id)
            )
        assert projected == "queued"
        same_table = VersionedRecordStore(database, "runtime_runs")
        await same_table.update(record.id, {**record.data, "status": "completed"}, record.revision)
        assert await runs.count_statuses(("completed",)) == 7
        for invalid in ({"limit": 0}, {"offset": -1}, {"after_id": cursor, "offset": 1}):
            with pytest.raises(OpsMeshError):
                await runs.select_runs(**invalid)
    finally:
        await database.close()


async def test_stream_query_is_filtered_and_bounded_in_sql(tmp_path, monkeypatch):
    database = AsyncDatabase(f"sqlite:///{(tmp_path / 'stream.db').as_posix()}")
    streams = RunStreamStore(database)
    await database.initialize()
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    async def forbid_scan(*_args, **_kwargs):
        raise AssertionError("Stream readers must not load unrelated events")

    try:
        for run_id in ("one", "other"):
            for seq in range(1, 7):
                await streams.create(f"{run_id}-{seq}", {"run_id": run_id, "seq": seq})
        monkeypatch.setattr(streams, "list", forbid_scan)
        event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
        assert [row.data["seq"] for row in await streams.read("one", 2, limit=2)] == [3, 4]
        query = statements[-1]
        assert "where runtime_stream.run_id" in query and "runtime_stream.seq >" in query
        assert "order by runtime_stream.seq" in query and "limit" in query
    finally:
        await database.close()
