import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.events import DispatchContext, OpsMeshEvent
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.scheduling.api import PersistentSchedule
from opsmesh.modules.scheduling.internal.service import PersistentScheduler


async def authorize(actor: str, action: str, resource: str) -> None:
    if actor != "owner":
        raise PermissionError("unknown owner")


async def resolve_agent(reference: str) -> AgentSpecification:
    return AgentSpecification(id="agent", name="Agent")


async def completed_run(run_id: str) -> AgentRunView:
    return AgentRunView(
        id=run_id,
        revision=1,
        principal_id="owner",
        session_id="session",
        agent=await resolve_agent("agent@1"),
        status="completed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_two_schedulers_claim_once_and_restart_retains_next_run(tmp_path: Path) -> None:
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'schedules.sqlite').as_posix()}")
    store = VersionedRecordStore(database, "schedules")
    await database.initialize()
    observed: list[DispatchContext[OpsMeshEvent]] = []

    async def dispatch(context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
        observed.append(context)
        context.values["run_id"] = context.event.id
        await asyncio.sleep(0)
        return DispatchResult(context.event.id, matched=1)

    first = PersistentScheduler(
        OpsMeshDriver(),
        store,
        authorize,
        dispatch,
        agent_resolver=resolve_agent,
        run_resolver=completed_run,
    )
    second = PersistentScheduler(
        OpsMeshDriver(),
        store,
        authorize,
        dispatch,
        agent_resolver=resolve_agent,
        run_resolver=completed_run,
    )
    due = datetime.now(UTC)
    try:
        await first.create(
            PersistentSchedule(
                id="daily",
                principal_id="owner",
                next_run_at=due,
                interval_seconds=30,
                agent_revision="agent@1",
            ),
            actor_id="owner",
        )
        results = await asyncio.gather(first.tick(now=due), second.tick(now=due))
        assert sum(results) == 1 and len(observed) == 1
        assert observed[0].values["agent_revision"] == "agent@1"
        assert observed[0].values["idempotency_key"] == observed[0].event.id
        assert observed[0].bot is None
        restarted = PersistentScheduler(
            OpsMeshDriver(),
            store,
            authorize,
            dispatch,
            agent_resolver=resolve_agent,
            run_resolver=completed_run,
        )
        assert await restarted.tick(now=due) == 0
        assert await restarted.tick(now=due + timedelta(seconds=30)) == 1
        assert observed[0].event.id != observed[1].event.id
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_failed_schedule_is_disabled_until_explicit_reenable(tmp_path: Path) -> None:
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'failed.sqlite').as_posix()}")
    store = VersionedRecordStore(database, "schedules")
    await database.initialize()

    async def fail(context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
        raise RuntimeError("failed after possible side effect")

    scheduler = PersistentScheduler(OpsMeshDriver(), store, authorize, fail)
    try:
        await scheduler.create(
            PersistentSchedule(id="one", principal_id="owner", next_run_at=datetime.now(UTC)),
            actor_id="owner",
        )
        assert await scheduler.tick() == 0
        assert len(scheduler.errors) == 1
        assert not (await scheduler.list_schedules(actor_id="owner"))[0].enabled
        assert await scheduler.tick() == 0
        assert len(scheduler.errors) == 1
    finally:
        await database.close()


async def test_single_run_process_does_not_start_scheduler(tmp_path: Path) -> None:
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'worker.sqlite').as_posix()}")
    store = VersionedRecordStore(database, "schedules")
    await database.initialize()

    async def unexpected(context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
        raise AssertionError("Single-run process must not poll persistent schedules")

    driver = OpsMeshDriver()
    scheduler = PersistentScheduler(driver, store, authorize, unexpected, autostart=False)
    try:
        await scheduler.create(
            PersistentSchedule(id="due", principal_id="owner", next_run_at=datetime.now(UTC)),
            actor_id="owner",
        )
        await driver.start()
        await asyncio.sleep(0)
        assert scheduler._task is None
        assert (await store.get("due")).data["state"] == "ready"
    finally:
        await driver.stop()
        await database.close()
