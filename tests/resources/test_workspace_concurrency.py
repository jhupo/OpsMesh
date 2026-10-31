import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.workspaces.api import WorkspaceEntry, WorkspaceManifest
from opsmesh.modules.workspaces.internal import service as workspace_module
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


@pytest_asyncio.fixture
async def shared_workspaces(
    tmp_path: Path,
) -> AsyncIterator[tuple[LocalWorkspaceService, LocalWorkspaceService]]:
    url = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    databases = (AsyncDatabase(url), AsyncDatabase(url))
    services = tuple(
        LocalWorkspaceService(
            database, tmp_path / "files", mutation_lease_seconds=0.6, mutation_wait_seconds=5
        )
        for database in databases
    )
    await databases[0].initialize()
    await databases[1].verify_schema()
    await services[0].create("project")
    try:
        yield services
    finally:
        await asyncio.gather(*(database.close() for database in databases))


async def test_two_services_cannot_materialize_over_the_same_readonly_path(
    shared_workspaces,
) -> None:
    first, second = shared_workspaces
    results = await asyncio.gather(
        *(
            service.materialize(
                "project",
                WorkspaceManifest(
                    entries=(WorkspaceEntry(path="shared.txt", content=content, read_only=True),)
                ),
            )
            for service, content in ((first, "first"), (second, "second"))
        ),
        return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, OpsMeshError)]
    assert len(failures) == 1 and failures[0].code == "CONFLICT"
    before = await first.read("project", "shared.txt")
    with pytest.raises(OpsMeshError) as denied:
        await second.write("project", "shared.txt", b"overwrite")
    assert denied.value.code == "AUTHORIZATION_DENIED"
    assert await second.read("project", "shared.txt") == before


async def test_snapshot_waits_for_whole_manifest_and_lease_renews(
    shared_workspaces, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = shared_workspaces
    midway, proceed = asyncio.Event(), asyncio.Event()
    original_write = first.write

    async def paused_write(workspace_id: str, path: str, data: bytes, *, read_only: bool = False):
        result = await original_write(workspace_id, path, data, read_only=read_only)
        if path == "a.txt":
            midway.set()
            await proceed.wait()
        return result

    monkeypatch.setattr(first, "write", paused_write)
    writer = asyncio.create_task(
        first.materialize(
            "project",
            WorkspaceManifest(
                entries=(
                    WorkspaceEntry(path="a.txt", content="a"),
                    WorkspaceEntry(path="b.txt", content="b"),
                )
            ),
        )
    )
    await asyncio.wait_for(midway.wait(), timeout=3)
    snapshot_task = asyncio.create_task(second.snapshot("project"))
    await asyncio.sleep(0.8)  # Longer than the original lease: the heartbeat must retain it.
    assert not snapshot_task.done()
    proceed.set()
    await writer
    snapshot = await snapshot_task
    assert {entry.path for entry in snapshot.entries} == {"a.txt", "b.txt"}
    await second.restore(snapshot.id, "restored")  # Reentrant create/materialize/write.
    assert await first.read("restored", "b.txt") == b"b"
    await first.delete("restored")


async def test_old_fence_cannot_write_or_release_new_owner(shared_workspaces) -> None:
    first, second = shared_workspaces
    import hashlib

    key = hashlib.sha256(f"{first._locks.scope}:project".encode()).hexdigest()
    old = await first._locks._acquire(key)
    record = await first._locks.store.get(key)
    assert record is not None
    await first._locks.store.update(key, {**record.data, "expires_at": 0}, record.revision)
    async with second._locks.hold("project") as replacement:
        assert replacement.fence > old.fence
        with pytest.raises(OpsMeshError) as fenced:
            await old.run(lambda: (_ for _ in ()).throw(AssertionError("Must not execute")))
        assert fenced.value.code == "WORKSPACE_LEASE_LOST"
        await first._locks._release(old)
        await replacement.run(lambda: None)
        await second.write("project", "current.txt", b"new owner")


async def test_cancelled_filesystem_thread_finishes_before_new_writer_can_replace(
    shared_workspaces, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = shared_workspaces
    entered, proceed = threading.Event(), threading.Event()
    original = workspace_module.atomic_write

    def blocked_write(path, data, *, read_only=False):
        if path.name == "target.txt" and data == b"old":
            entered.set()
            assert proceed.wait(timeout=5)
        return original(path, data, read_only=read_only)

    monkeypatch.setattr(workspace_module, "atomic_write", blocked_write)
    old_task = asyncio.create_task(first.write("project", "target.txt", b"old"))
    assert await asyncio.to_thread(entered.wait, 3)
    old_task.cancel()
    replacement = asyncio.create_task(second.write("project", "target.txt", b"new"))
    await asyncio.sleep(0.05)
    assert not replacement.done()
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await old_task
    await replacement
    assert await first.read("project", "target.txt") == b"new"
