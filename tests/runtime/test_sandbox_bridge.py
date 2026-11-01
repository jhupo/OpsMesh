"""Durable workspace capture against real platform storage and a controlled sandbox transport."""

import io
import tarfile
from datetime import UTC, datetime

import pytest

from opsmesh.application.sandbox_bridge import SandboxWorkspaceBridge, archive_files
from opsmesh.framework.agents import AgentRunCompleted, AgentSpecification, SandboxConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


def make_archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def view():
    return AgentRunView(
        id="run",
        revision=1,
        principal_id="user",
        session_id="session",
        agent=AgentSpecification(
            id="a",
            name="A",
            workspace_id="source",
            sandbox=SandboxConfiguration(provider="docker", options={"image": "python:3.14-slim"}),
        ),
        status="running",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_workspace_checkpoint_resume_and_finalization(tmp_path, monkeypatch):
    from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
        ManagedSandboxSession,
    )

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'workspace.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "workspaces")
    bridge = SandboxWorkspaceBridge(workspaces, database, session_factory=ManagedSandboxSession)
    await database.initialize()
    opened = []

    class Transport:
        session = object()
        closed = False

        async def archive(self):
            return make_archive({"input.txt": b"original", "result.txt": b"produced"})

        async def close(self):
            self.closed = True

        def cleanup_state(self):
            return {"id": "sandbox"}

        async def materialize_files(self, files):
            pass

    async def open_native(configuration, files, directories):
        transport = Transport()
        opened.append((files, transport))
        return transport

    monkeypatch.setattr(ManagedSandboxSession, "open", open_native)
    try:
        await workspaces.create("source")
        await workspaces.write("source", "input.txt", b"original")
        context = await bridge.prepare(view())
        assert opened[0][0][0][1] == b"original"
        checkpoint = await context["checkpoint_resources"]()
        assert opened[0][1].closed
        assert await workspaces.read(checkpoint["workspace_id"], "result.txt") == b"produced"
        fresh = SandboxWorkspaceBridge(workspaces, database, session_factory=ManagedSandboxSession)
        resumed = await fresh.prepare(view())
        assert any(path == "result.txt" for path, _, _ in opened[-1][0])
        metadata = await fresh.finalize(view(), AgentRunCompleted(output="done"))
        assert opened[-1][1].closed
        assert len(metadata["artifacts"]) == 2
        await resumed["cleanup_resources"]()
        assert (await fresh.finalize(view(), AgentRunCompleted(output="done"))) == metadata
        assert await workspaces.read("source", "input.txt") == b"original"
    finally:
        await bridge.close()
        await database.close()


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/host", "foo\\bar"])
def test_sandbox_archive_rejects_unsafe_paths(path):
    with pytest.raises(OpsMeshError):
        archive_files(make_archive({path: b"data"}), max_members=10, max_bytes=100)


def test_sandbox_archive_rejects_links_and_size_overflow():
    with pytest.raises(OpsMeshError):
        archive_files(make_archive({"large": b"12345"}), max_members=10, max_bytes=4)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/outside"
        archive.addfile(info)
    with pytest.raises(OpsMeshError):
        archive_files(stream.getvalue(), max_members=10, max_bytes=100)


def test_managed_manifest_rejects_host_bindings():
    from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
        validate_managed_manifest,
    )

    for manifest in (
        {"root": "/host"},
        {"entries": {"secret": {"type": "local_file", "src": "/etc/passwd"}}},
    ):
        with pytest.raises(OpsMeshError):
            validate_managed_manifest(SandboxConfiguration(manifest=manifest))


def test_archive_read_only_permissions_survive_capture():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo(".agents/skill/SKILL.md")
        info.mode = 0o444
        info.size = 4
        archive.addfile(info, io.BytesIO(b"read"))
    files, _ = archive_files(stream.getvalue(), max_members=10, max_bytes=100)
    assert files == [(".agents/skill/SKILL.md", b"read", True)]


@pytest.mark.asyncio
async def test_old_attempt_cleanup_cannot_close_replacement_sandbox(tmp_path, monkeypatch):
    from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
        ManagedSandboxSession,
    )

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'ownership.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "workspaces")
    bridge = SandboxWorkspaceBridge(workspaces, database, session_factory=ManagedSandboxSession)
    await database.initialize()
    opened = []

    class Transport:
        session = object()
        closed = False

        async def close(self):
            self.closed = True

        def cleanup_state(self):
            return {"id": str(id(self))}

        async def materialize_files(self, files):
            pass

    async def open_native(*args):
        transport = Transport()
        opened.append(transport)
        return transport

    monkeypatch.setattr(ManagedSandboxSession, "open", open_native)
    try:
        await workspaces.create("source")
        old = await bridge.prepare(view().model_copy(update={"attempt_id": "old"}))
        await bridge.prepare(view().model_copy(update={"attempt_id": "new"}))
        await old["cleanup_resources"]()
        assert opened[0].closed and not opened[1].closed
        record = await bridge.bindings.get("run")
        assert record.data["attempt_id"] == "new" and record.data["cleanup_state"]
    finally:
        await bridge.close()
        await database.close()
