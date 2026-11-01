"""Partially committed schedule materialization cannot consume capacity twice."""

from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.scheduling.api import PersistentSchedule
from opsmesh.modules.scheduling.internal.engine import ScheduleExecutionEngine
from opsmesh.modules.scheduling.internal.repository import ScheduleRepository


@pytest.mark.parametrize("crash_after", [1, 2])
@pytest.mark.parametrize("policy", ["queue", "skip"])
async def test_partial_materialization_resumes_without_false_capacity_skip(
    tmp_path, monkeypatch, crash_after, policy
):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'schedules.db').as_posix()}")
    store = VersionedRecordStore(database, "scheduler_records")
    repository = ScheduleRepository(store, 120)

    async def unused(*args):
        raise AssertionError("Materialization never dispatches")

    engine = ScheduleExecutionEngine(repository, unused, unused, None, None)
    await database.initialize()
    try:
        start = datetime.now(UTC) - timedelta(seconds=30)
        schedule = PersistentSchedule(
            id="plan",
            principal_id="owner",
            next_run_at=start,
            interval_seconds=10,
            misfire_policy="catch_up",
            max_catch_up=4,
            max_queued_occurrences=3,
            concurrency_policy=policy,
            max_active_runs=3,
        )
        await store.create(
            schedule.id,
            {
                "kind": "schedule",
                "schedule": schedule.model_dump(mode="json"),
                "actor_id": "owner",
            },
        )
        plan, owner = await repository.claim(schedule.id)
        original = repository.add_occurrence
        committed = 0

        async def crash_after_commit(*args):
            nonlocal committed
            saved = await original(*args)
            committed += 1
            if committed == crash_after:
                raise RuntimeError("Process lost after occurrence commit")
            return saved

        monkeypatch.setattr(repository, "add_occurrence", crash_after_commit)
        with pytest.raises(RuntimeError):
            await engine.materialize(plan, owner, start + timedelta(seconds=30))
        row = await repository.plan(schedule.id)
        await store.update(
            schedule.id,
            {
                **row.data,
                "lease_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
            row.revision,
        )
        monkeypatch.setattr(repository, "add_occurrence", original)
        restarted = ScheduleExecutionEngine(repository, unused, unused, None, None)
        plan, owner = await repository.claim(schedule.id)
        await restarted.materialize(plan, owner, start + timedelta(seconds=30))
        rows = sorted(
            await repository.occurrences(schedule.id),
            key=lambda item: item.data["occurrence"]["scheduled_at"],
        )
        assert [row.data["occurrence"]["status"] for row in rows] == [
            "queued",
            "queued",
            "queued",
            "skipped",
        ]
        assert rows[-1].data["occurrence"]["reason"] == (
            "queue_full" if policy == "queue" else "concurrency_limit"
        )
        updated = await repository.plan(schedule.id)
        assert datetime.fromisoformat(updated.data["schedule"]["next_run_at"]) == (
            start + timedelta(seconds=40)
        )
        facts = await database.pending_events()
        assert len(facts) == 4
    finally:
        await database.close()
