"""Obsolete memory jobs and interrupted workspace publication have durable outcomes."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from opsmesh.application.resource_references import PlatformResourceReferences
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.memory.api import (
    MemoryNamespace,
    MemoryNamespaceSettings,
    MemorySource,
)
from opsmesh.modules.memory.internal.service import PersistentMemoryService
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService
from opsmesh.modules.workspaces.management import WorkspaceManagement

from .test_memory_lifecycle import consolidator, extractor
from .test_resource_management import Journal


@pytest.fixture
async def resources(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'resources.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "content")
    owners = VersionedRecordStore(database, "workspace_owners")

    async def authorize(*args, **kwargs):
        return None

    async def references():
        return set()

    management = WorkspaceManagement(
        workspaces,
        authorize,
        Journal(),
        references,
        owners=owners,
        authorize_creation=authorize,
    )
    memory = PersistentMemoryService(database, workspaces, extractor, consolidator)
    await database.initialize()
    try:
        yield database, workspaces, owners, management, memory
    finally:
        await workspaces.close()
        await database.close()


async def memory_job(memory, key="job"):
    return await memory.request_generation("memory", principal_id="alice", idempotency_key=key)


async def prepare_memory(memory):
    await memory.create_namespace(MemoryNamespace(id="memory", owner="alice"))
    await memory.append_source(
        MemorySource(
            id="source", namespace_id="memory", conversation_id="c", run_id="r", text="keep"
        ),
        principal_id="alice",
    )


async def test_stale_memory_job_is_terminal_with_concurrent_workers(resources):
    database, workspaces, _owners, _management, memory = resources
    second = PersistentMemoryService(database, workspaces, extractor, consolidator)
    await prepare_memory(memory)
    job = await memory_job(memory)
    await memory.configure_namespace(
        "memory",
        MemoryNamespaceSettings(readers=("bob",)),
        principal_id="alice",
        expected_revision=1,
    )
    outcomes = await asyncio.gather(memory.run_job(job.id), second.run_job(job.id))
    assert all(
        item.status == "failed" and item.error_code == "MEMORY_NAMESPACE_CHANGED"
        for item in outcomes
    )
    assert await memory.list_pending_jobs() == ()
    assert (await memory.run_job(job.id)).status == "failed"
    replacement = await memory_job(memory, "new-version")
    assert (await second.run_job(replacement.id)).status == "succeeded"


async def test_deleted_namespace_jobs_are_invalidated_during_queue_scan(resources):
    _database, _workspaces, _owners, _management, memory = resources
    await prepare_memory(memory)
    job = await memory_job(memory)
    await memory.forget("memory", principal_id="alice")
    assert await memory.list_pending_jobs() == ()
    row = await memory._jobs.get(job.id)
    assert row.data["job"]["status"] == "failed"
    assert row.data["job"]["error_code"] == "MEMORY_NAMESPACE_UNAVAILABLE"


async def source_snapshot(management):
    await management.create("source", actor_id="alice")
    await management.write("source", "document.txt", b"original", actor_id="alice", read_only=True)
    await management.write("source", "last.txt", b"second", actor_id="alice")
    return await management.snapshot("source", actor_id="alice")


async def test_restore_retries_same_operation_and_rejects_untyped_reservation(
    resources, monkeypatch
):
    _database, workspaces, owners, management, _memory = resources
    snapshot = await source_snapshot(management)
    original = workspaces.get_blob
    calls = 0

    async def unavailable(digest):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await original(digest)
        raise OpsMeshError("STORAGE_UNAVAILABLE", "Injected transient storage failure")

    monkeypatch.setattr(workspaces, "get_blob", unavailable)
    with pytest.raises(OpsMeshError, match="transient"):
        await management.restore(snapshot.id, "destination", actor_id="alice")
    assert "destination" not in {item.id for item in await workspaces.list()}
    with pytest.raises(OpsMeshError) as partial:
        await workspaces.read("destination", "document.txt")
    assert partial.value.code == "WORKSPACE_RESTORING"
    with pytest.raises(OpsMeshError):
        await management.create("destination", actor_id="alice")
    with pytest.raises(OpsMeshError):
        await management.restore(snapshot.id, "destination", actor_id="bob")
    monkeypatch.setattr(workspaces, "get_blob", original)
    restored = await management.restore(snapshot.id, "destination", actor_id="alice")
    assert await workspaces.read(restored.id, "document.txt") == b"original"
    assert (await workspaces.stat(restored.id, "document.txt")).read_only
    assert await management.restore(snapshot.id, "destination", actor_id="alice") == restored
    await owners.create("legacy", {"owner_id": "alice"})
    with pytest.raises(OpsMeshError) as reserved:
        await management.restore(snapshot.id, "legacy", actor_id="alice")
    assert reserved.value.code == "CONFLICT"
    assert (await owners.get("legacy")).data == {"owner_id": "alice"}


async def test_pending_restore_pins_snapshot_and_completion_releases_reference(
    resources, monkeypatch
):
    database, workspaces, _owners, management, _memory = resources
    references = PlatformResourceReferences(
        accounts=SimpleNamespace(),
        agents=SimpleNamespace(),
        runtime=SimpleNamespace(),
        database=database,
    )
    management._snapshot_references = references.snapshots
    snapshot = await source_snapshot(management)
    original = workspaces.get_blob

    async def unavailable(digest):
        raise OpsMeshError("STORAGE_UNAVAILABLE", "Temporarily unavailable")

    monkeypatch.setattr(workspaces, "get_blob", unavailable)
    with pytest.raises(OpsMeshError):
        await management.restore(snapshot.id, "target", actor_id="alice")
    assert snapshot.id in await references.snapshots()
    with pytest.raises(OpsMeshError) as in_use:
        await management.delete_snapshot(snapshot.id, actor_id="alice")
    assert in_use.value.code == "RESOURCE_IN_USE"
    monkeypatch.setattr(workspaces, "get_blob", original)
    await management.restore(snapshot.id, "target", actor_id="alice")
    assert snapshot.id not in await references.snapshots()
    await management.delete_snapshot(snapshot.id, actor_id="alice")
    assert await workspaces.read("target", "document.txt") == b"original"


async def test_ready_restore_does_not_report_success_for_missing_directory(resources):
    _database, workspaces, _owners, management, _memory = resources
    snapshot = await source_snapshot(management)
    await management.restore(snapshot.id, "target", actor_id="alice")
    target = await workspaces.root_path("target")
    target.rename(target.with_name("moved-by-external-operator"))
    with pytest.raises(OpsMeshError) as missing:
        await management.restore(snapshot.id, "target", actor_id="alice")
    assert missing.value.code == "WORKSPACE_UNAVAILABLE"


async def test_raw_restore_without_key_can_retry_an_incomplete_operation(resources, monkeypatch):
    _database, workspaces, _owners, management, _memory = resources
    snapshot = await source_snapshot(management)
    original = workspaces.get_blob
    calls = 0

    async def unavailable(digest):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await original(digest)
        raise OpsMeshError("STORAGE_UNAVAILABLE", "Transient failure after first file")

    monkeypatch.setattr(workspaces, "get_blob", unavailable)
    with pytest.raises(OpsMeshError, match="Transient"):
        await workspaces.restore(snapshot.id, "target")
    monkeypatch.setattr(workspaces, "get_blob", original)
    await workspaces.restore(snapshot.id, "target")
    assert await workspaces.read("target", "document.txt") == b"original"
    assert await workspaces.read("target", "last.txt") == b"second"
    with pytest.raises(OpsMeshError) as occupied:
        await workspaces.restore(snapshot.id, "target")
    assert occupied.value.code == "CONFLICT"


async def test_raw_create_cannot_mutate_an_incomplete_restore_target(resources, monkeypatch):
    _database, workspaces, _owners, management, _memory = resources
    snapshot = await source_snapshot(management)
    original_read = workspaces.get_blob

    async def unavailable(digest):
        raise OpsMeshError("STORAGE_UNAVAILABLE", "Transient storage failure")

    monkeypatch.setattr(workspaces, "get_blob", unavailable)
    with pytest.raises(OpsMeshError, match="Transient"):
        await workspaces.restore(snapshot.id, "target", idempotency_key="operation")
    monkeypatch.setattr(workspaces, "get_blob", original_read)
    target = workspaces._files / "target"
    original_mkdir = Path.mkdir
    touched = []

    def observe_mkdir(path, *args, **kwargs):
        if path == target:
            touched.append(path)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", observe_mkdir)
    with pytest.raises(OpsMeshError) as reserved:
        await workspaces.create("target")
    assert reserved.value.code == "CONFLICT"
    # Even a temporary empty directory is unsafe: cancellation after mkdir could
    # leave it behind and make the interrupted restore appear already published.
    assert touched == [] and not target.exists()
    await workspaces.restore(snapshot.id, "target", idempotency_key="operation")
    assert await workspaces.read("target", "document.txt") == b"original"
    assert await workspaces.read("target", "last.txt") == b"second"


async def test_restore_staging_cannot_follow_external_symlink(resources, tmp_path):
    _database, workspaces, _owners, management, _memory = resources
    snapshot = await source_snapshot(management)
    staging = workspaces._restoration.staging
    staging.rmdir()
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_text("untouched")
    try:
        staging.symlink_to(external, target_is_directory=True)
    except OSError:
        staging.mkdir(exist_ok=True)
        pytest.skip("Directory symlinks are unavailable")
    try:
        with pytest.raises(OpsMeshError) as unsafe:
            await management.restore(snapshot.id, "target", actor_id="alice")
        assert unsafe.value.code == "PATH_INVALID"
        assert sentinel.read_text() == "untouched"
    finally:
        staging.unlink()


async def test_cancelled_restore_is_hidden_and_resumes_after_reconstruction(
    resources, monkeypatch, tmp_path
):
    database, workspaces, owners, management, _memory = resources
    snapshot = await source_snapshot(management)
    entered = asyncio.Event()

    async def blocked(digest):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(workspaces, "get_blob", blocked)
    operation = asyncio.create_task(management.restore(snapshot.id, "target", actor_id="alice"))
    await asyncio.wait_for(entered.wait(), 2)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    second = LocalWorkspaceService(database, tmp_path / "content")
    restarted = WorkspaceManagement(
        second,
        management._authorize,
        Journal(),
        management._snapshot_references,
        owners=owners,
        authorize_creation=management._authorize_creation,
    )
    try:
        await restarted.restore(snapshot.id, "target", actor_id="alice")
        assert await second.read("target", "document.txt") == b"original"
    finally:
        await second.close()


async def test_restore_recovers_after_publication_without_overwriting_later_edits(
    resources, monkeypatch
):
    _database, workspaces, _owners, management, _memory = resources
    snapshot = await source_snapshot(management)
    original = workspaces._workspaces.update

    async def fail_completion(record_id, data, expected_revision, **kwargs):
        if data.get("restore_state") == "ready":
            raise OpsMeshError("STORAGE_UNAVAILABLE", "Completion commit interrupted")
        return await original(record_id, data, expected_revision, **kwargs)

    monkeypatch.setattr(workspaces._workspaces, "update", fail_completion)
    with pytest.raises(OpsMeshError, match="Completion"):
        await management.restore(snapshot.id, "target", actor_id="alice")
    monkeypatch.setattr(workspaces._workspaces, "update", original)

    async def unavailable(digest):
        raise AssertionError("Already published content must not be downloaded again")

    monkeypatch.setattr(workspaces, "get_blob", unavailable)
    await management.restore(snapshot.id, "target", actor_id="alice")
    await management.write("target", "new.txt", b"later edit", actor_id="alice")
    await management.restore(snapshot.id, "target", actor_id="alice")
    assert await workspaces.read("target", "new.txt") == b"later edit"


async def test_competing_create_cannot_take_restore_target(resources, monkeypatch):
    _database, workspaces, _owners, management, _memory = resources
    snapshot = await source_snapshot(management)
    original = workspaces.get_blob
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(digest):
        entered.set()
        await release.wait()
        return await original(digest)

    monkeypatch.setattr(workspaces, "get_blob", blocked)
    restore = asyncio.create_task(management.restore(snapshot.id, "target", actor_id="alice"))
    await asyncio.wait_for(entered.wait(), 2)
    create = asyncio.create_task(management.create("target", actor_id="alice"))
    replay = asyncio.create_task(management.restore(snapshot.id, "target", actor_id="alice"))
    release.set()
    results = await asyncio.gather(restore, create, replay, return_exceptions=True)
    assert results[0] == results[1] == results[2]
    assert await workspaces.read("target", "document.txt") == b"original"
