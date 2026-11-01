"""Workspace management keeps actor filtering, path safety and snapshot references."""

import hashlib
import stat

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.workspaces.internal import paths
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService
from opsmesh.modules.workspaces.management import WorkspaceManagement

from .test_resource_management import Journal


def test_managed_file_replace_failure_restores_original_read_only_file(tmp_path, monkeypatch):
    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"trusted")
    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    def fail_replace(source, destination):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(paths.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        paths.replace_managed_file(
            target, b"new", expected_digest=hashlib.sha256(b"trusted").hexdigest(), read_only=True
        )
    assert target.read_bytes() == b"trusted"
    assert not target.stat().st_mode & stat.S_IWUSR
    assert list(tmp_path.iterdir()) == [target]


async def test_workspace_file_operations_preserve_readonly_and_boundaries(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    await database.initialize()
    try:
        await workspaces.create("workspace")
        await workspaces.write("workspace", "folder/editable", b"text")
        await workspaces.write("workspace", "folder/locked", b"secret", read_only=True)
        renamed = await workspaces.rename("workspace", "folder/editable", "folder/renamed")
        assert renamed.path == "folder/renamed" and renamed.size == 4
        with pytest.raises(OpsMeshError, match="read-only"):
            await workspaces.rename("workspace", "folder", "moved")
        with pytest.raises(OpsMeshError, match="read-only"):
            await workspaces.delete_file("workspace", "folder/locked")
        for path in ("../escape", "C:/absolute", "folder\\renamed"):
            with pytest.raises(OpsMeshError):
                await workspaces.stat("workspace", path)
        assert len(await workspaces.tree("workspace")) == 3
        await workspaces.delete_file("workspace", "folder/renamed")
        with pytest.raises(OpsMeshError):
            await workspaces.read("workspace", "folder/renamed")
    finally:
        await workspaces.close()
        await database.close()


async def test_workspace_list_and_snapshot_deletion_are_authorized(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "data")
    referenced = set()

    async def authorize(actor, workspace, *, write=False):
        if actor != workspace:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Denied")

    async def references():
        return referenced

    journal = Journal()

    async def authorize_creation(actor, action, workspace):
        await authorize(actor, workspace, write=True)

    management = WorkspaceManagement(
        workspaces,
        authorize,
        journal,
        references,
        owners=VersionedRecordStore(database, "workspace_owners"),
        authorize_creation=authorize_creation,
    )
    await database.initialize()
    try:
        await workspaces.create("alice")
        await workspaces.create("bob")
        assert [workspace.id for workspace in await management.list(actor_id="alice")] == ["alice"]
        snapshot = await management.snapshot("alice", actor_id="alice")
        referenced.add(snapshot.id)
        with pytest.raises(OpsMeshError, match="referenced"):
            await management.delete_snapshot(snapshot.id, actor_id="alice")
        with pytest.raises(OpsMeshError):
            await management.delete_snapshot(snapshot.id, actor_id="bob")
        referenced.clear()
        await management.delete_snapshot(snapshot.id, actor_id="alice")
        assert await management.list_snapshots("alice", actor_id="alice") == ()
        retained = await workspaces._snapshots._records.get(snapshot.id)
        assert retained is not None and retained.data["deleted"]
        with pytest.raises(OpsMeshError):
            await workspaces.get_snapshot(snapshot.id)
        assert any(
            record.action == "workspace.delete_snapshot" and record.outcome == "succeeded"
            for record in journal.records
        )
    finally:
        await workspaces.close()
        await database.close()
