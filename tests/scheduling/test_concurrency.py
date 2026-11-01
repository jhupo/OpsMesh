import asyncio
from datetime import UTC, datetime, timedelta

from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.scheduling.api import PersistentSchedule
from opsmesh.modules.scheduling.internal import repository as repository_module
from opsmesh.modules.scheduling.internal.service import PersistentScheduler


async def allow(actor, action, resource):
    return None


async def test_fresh_clock_per_claim_prevents_repeated_second_dispatch(tmp_path, monkeypatch):
    due = datetime.now(UTC)
    lease_time = due

    class LeaseClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return lease_time.astimezone(tz)

    # Advance persisted lease time explicitly, independently of I/O and the event loop.
    monkeypatch.setattr(repository_module, "datetime", LeaseClock)
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
        nonlocal lease_time
        observed.append(context.event.job_id)
        if context.event.job_id == "first":
            lease_time = due + timedelta(seconds=40)
        else:
            second_started.set()
            await finish_second.wait()
        return DispatchResult(context.event.id, matched=1)

    first = PersistentScheduler(OpsMeshDriver(), store, allow, dispatch, lease_seconds=60)
    second = PersistentScheduler(OpsMeshDriver(), second_store, allow, dispatch, lease_seconds=60)
    task = None
    try:
        for identifier in ("first", "second"):
            await first.create(
                PersistentSchedule(id=identifier, principal_id="owner", next_run_at=due),
                actor_id="owner",
            )
        task = asyncio.create_task(first.tick(now=due))
        await asyncio.wait_for(second_started.wait(), 10)
        # A lease based on the batch start expired at t+60; the second claim is live to t+100.
        lease_time = due + timedelta(seconds=70)
        plan = await store.get("second")
        assert plan is not None
        expiry = datetime.fromisoformat(plan.data["lease_expires_at"])
        assert due + timedelta(seconds=60) < lease_time < expiry
        assert await second.tick(now=lease_time) == 0
        finish_second.set()
        assert await asyncio.wait_for(task, 10) == 2
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
