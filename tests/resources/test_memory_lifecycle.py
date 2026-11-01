"""Published live updates and explicit retention preserve active job inputs."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.memory.api import (
    MemoryCandidate,
    MemoryExtraction,
    MemoryNamespace,
    MemoryPolicy,
    MemorySource,
)
from opsmesh.modules.memory.internal.service import PersistentMemoryService
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


async def extractor(source):
    return MemoryExtraction(source_id=source.id, summary="summary", notes=source.text)


async def consolidator(items, prior):
    return MemoryCandidate(summary="summary", index="index")


@pytest.mark.asyncio
async def test_live_update_permissions_idempotency_and_competing_revision(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "content")
    memory = PersistentMemoryService(database, workspaces, extractor, consolidator)
    await database.initialize()
    try:
        await memory.create_namespace(
            MemoryNamespace(
                id="p", owner="alice", readers=("bob",), policy=MemoryPolicy(live_update=True)
            )
        )
        candidate = MemoryCandidate(summary="keep", index="helpful", usage={"fake": 0})
        with pytest.raises(OpsMeshError):
            await memory.update(
                "p", candidate, principal_id="bob", expected_revision=1, idempotency_key="u"
            )
        results = await asyncio.gather(
            *[
                memory.update(
                    "p",
                    candidate.model_copy(update={"index": str(index)}),
                    principal_id="alice",
                    expected_revision=1,
                    idempotency_key=str(index),
                )
                for index in range(2)
            ],
            return_exceptions=True,
        )
        winners = [result for result in results if isinstance(result, MemoryNamespace)]
        assert len(winners) == 1
        winner = winners[0]
        content = await memory.read("p", principal_id="alice")
        again = await memory.update(
            "p", content, principal_id="alice", expected_revision=1, idempotency_key=content.index
        )
        assert again.memory_revision == winner.memory_revision == 1 and content.usage == {}
        with pytest.raises(OpsMeshError):
            await memory.update(
                "p",
                candidate,
                principal_id="alice",
                expected_revision=2,
                idempotency_key=content.index,
            )
        await memory.create_namespace(MemoryNamespace(id="readonly", owner="alice"))
        with pytest.raises(OpsMeshError):
            await memory.update(
                "readonly",
                candidate,
                principal_id="alice",
                expected_revision=1,
                idempotency_key="disabled",
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_retention_keeps_queued_inputs_and_published_blob(tmp_path, monkeypatch):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "content")
    memory = PersistentMemoryService(database, workspaces, extractor, consolidator)
    await database.initialize()
    try:
        await memory.create_namespace(
            MemoryNamespace(
                id="p",
                owner="alice",
                policy=MemoryPolicy(source_retention_seconds=1, job_retention_seconds=1),
            )
        )
        await memory.append_source(
            MemorySource(id="s", namespace_id="p", conversation_id="c", run_id="r", text="source"),
            principal_id="alice",
        )
        job = await memory.request_generation("p", principal_id="alice", idempotency_key="job")
        future = datetime.now(UTC) + timedelta(days=1)

        class Future(datetime):
            @classmethod
            def now(cls, tz=None):
                return future

        monkeypatch.setattr("opsmesh.modules.memory.internal.retention.datetime", Future)
        first = await memory.prune("p", principal_id="alice")
        assert first.sources_deleted == first.jobs_deleted == 0
        await memory.run_job(job.id)
        second = await memory.prune("p", principal_id="alice")
        assert second.sources_deleted == second.jobs_deleted == 1
        assert second.stage_results_deleted == 2
        assert (await memory.read("p", principal_id="alice")).index == "index"
        assert (await memory.prune("p", principal_id="alice")).sources_deleted == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_retention_cannot_delete_inputs_of_concurrent_job_creation(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'race.db'}"
    databases = [AsyncDatabase(url), AsyncDatabase(url)]
    stores = [LocalWorkspaceService(db, tmp_path / "content") for db in databases]
    services = [
        PersistentMemoryService(db, store, extractor, consolidator)
        for db, store in zip(databases, stores, strict=True)
    ]
    for db in databases:
        await db.initialize()
    started, release = asyncio.Event(), asyncio.Event()
    original = databases[0].enqueue_events

    async def block_commit(connection, events):
        if events:
            started.set()
            await release.wait()
        await original(connection, events)

    try:
        await services[0].create_namespace(
            MemoryNamespace(
                id="race", owner="alice", policy=MemoryPolicy(source_retention_seconds=1)
            )
        )
        await services[0].append_source(
            MemorySource(
                id="s", namespace_id="race", conversation_id="c", run_id="r", text="needed"
            ),
            principal_id="alice",
        )
        future = datetime.now(UTC) + timedelta(days=1)

        class Future(datetime):
            @classmethod
            def now(cls, tz=None):
                return future

        monkeypatch.setattr("opsmesh.modules.memory.internal.retention.datetime", Future)
        monkeypatch.setattr(databases[0], "enqueue_events", block_commit)
        create = asyncio.create_task(
            services[0].request_generation("race", principal_id="alice", idempotency_key="job")
        )
        await started.wait()
        prune = asyncio.create_task(services[1].prune("race", principal_id="alice"))
        await asyncio.sleep(0.03)
        assert not prune.done()
        release.set()
        job, cleanup = await asyncio.gather(create, prune)
        assert cleanup.sources_deleted == 0
        assert (await services[1].run_job(job.id)).status == "succeeded"
    finally:
        release.set()
        for db in databases:
            await db.close()
