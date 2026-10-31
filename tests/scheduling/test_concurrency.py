import asyncio
from datetime import UTC, datetime

from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.scheduling.api import PersistentSchedule
from opsmesh.modules.scheduling.internal.service import PersistentScheduler


async def allow(actor, action, resource):
    return None


async def test_fresh_clock_per_claim_prevents_repeated_second_dispatch(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'multi.sqlite').as_posix()}"
    database = AsyncDatabase(url)
    second_database = AsyncDatabase(url)
    store = VersionedRecordStore(database, "schedules")
    second_store = VersionedRecordStore(second_database, "schedules")
    await database.initialize()
    await second_database.verify_schema()
    observed = []
    second_started = asyncio.Event()
    finish_second = asyncio.Event()

    async def dispatch(context):
        observed.append(context.event.job_id)
        if context.event.job_id == "first":
            await asyncio.sleep(0.18)
        else:
            second_started.set()
            await finish_second.wait()
        return DispatchResult(context.event.id, matched=1)

    first = PersistentScheduler(OpsMeshDriver(), store, allow, dispatch, lease_seconds=0.3)
    second = PersistentScheduler(OpsMeshDriver(), second_store, allow, dispatch, lease_seconds=0.3)
    task = None
    try:
        due = datetime.now(UTC)
        for identifier in ("first", "second"):
            await first.create(
                PersistentSchedule(id=identifier, principal_id="owner", next_run_at=due),
                actor_id="owner",
            )
        task = asyncio.create_task(first.tick(now=due))
        await asyncio.wait_for(second_started.wait(), 2)
        # The old batch-start lease is expired; the second task's fresh lease is live.
        await asyncio.sleep(0.14)
        assert await second.tick(now=datetime.now(UTC)) == 0
        finish_second.set()
        assert await task == 2
        assert observed == ["first", "second"]
        assert first.errors == [] and second.errors == []
    finally:
        finish_second.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await database.close()
        await second_database.close()


async def test_management_cannot_replace_configuration_during_dispatch(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'busy.sqlite').as_posix()}")
    store = VersionedRecordStore(database, "schedules")
    await database.initialize()
    entered, release = asyncio.Event(), asyncio.Event()

    async def dispatch(context):
        entered.set()
        await release.wait()
        return DispatchResult(context.event.id, matched=1)

    scheduler = PersistentScheduler(OpsMeshDriver(), store, allow, dispatch)
    task = None
    try:
        created = await scheduler.create(
            PersistentSchedule(id="busy", principal_id="owner"), actor_id="owner"
        )
        task = asyncio.create_task(scheduler.tick())
        await entered.wait()
        try:
            await scheduler.update(
                "busy", created.model_copy(update={"input": "different"}), 1, actor_id="owner"
            )
            raise AssertionError("A live lease must reject configuration changes")
        except OpsMeshError as exc:
            assert exc.code == "CONFLICT"
        release.set()
        assert await task == 1
        unchanged = await scheduler.get("busy", actor_id="owner")
        assert unchanged.input is None and unchanged.revision == 1
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await database.close()


async def test_expired_owner_cannot_overwrite_successor_occurrence(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'fenced.sqlite').as_posix()}")
    store = VersionedRecordStore(database, "schedules")
    await database.initialize()

    async def takeover(context):
        plan = await store.get("fenced")
        await store.update("fenced", {**plan.data, "lease_owner": "successor"}, plan.revision)
        occurrence_id = context.values["occurrence_id"]
        row = await store.get(occurrence_id)
        await store.update(
            row.id,
            {
                **row.data,
                "occurrence": {
                    **row.data["occurrence"],
                    "status": "completed",
                    "reason": "successor_acknowledged",
                },
            },
            row.revision,
        )
        return DispatchResult(context.event.id, matched=1)

    scheduler = PersistentScheduler(OpsMeshDriver(), store, allow, takeover)
    try:
        await scheduler.create(
            PersistentSchedule(id="fenced", principal_id="owner"), actor_id="owner"
        )
        assert await scheduler.tick() == 0
        (occurrence,) = await scheduler.list_occurrences("fenced", actor_id="owner")
        assert occurrence.status == "completed" and occurrence.reason == "successor_acknowledged"
        assert (await store.get("fenced")).data["lease_owner"] == "successor"
    finally:
        await database.close()
