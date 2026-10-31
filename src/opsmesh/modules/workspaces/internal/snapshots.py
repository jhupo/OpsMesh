"""Durable local content storage and workspace snapshots without archive extraction."""

import stat
from functools import partial
from pathlib import Path
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.workspaces.api import (
    ContentReferenceProvider,
    WorkspaceArtifact,
    WorkspaceService,
    WorkspaceSnapshot,
)
from opsmesh.modules.workspaces.events import WorkspaceSnapshotCreated

from .locking import WorkspaceMutationLease
from .paths import checked_path


def inspect_entry(root: Path, path: Path) -> tuple[bool, bool, bool]:
    checked_path(root, path.relative_to(root).as_posix())
    return path.is_dir(), path.is_file(), not bool(path.stat().st_mode & stat.S_IWUSR)


class WorkspaceSnapshots:
    def __init__(self, database: AsyncDatabase, workspaces: WorkspaceService) -> None:
        self._records = VersionedRecordStore(database, "workspace_snapshots")
        self._workspaces = workspaces

    async def list(self, workspace_id: str) -> tuple[WorkspaceSnapshot, ...]:
        return tuple(
            WorkspaceSnapshot.model_validate(row.data)
            for row in await self._records.list()
            if row.data["workspace_id"] == workspace_id and not row.data.get("deleted")
        )

    async def delete(self, snapshot_id: str, references: ContentReferenceProvider) -> None:
        row = await self._records.get(snapshot_id)
        if row is None or row.data.get("deleted"):
            raise OpsMeshError("NOT_FOUND", "Workspace snapshot not found")
        if snapshot_id in await references():
            raise OpsMeshError("RESOURCE_IN_USE", "Snapshot is referenced by a persisted resource")
        # Retain the manifest and blob references. A concurrent publisher can only
        # lose availability, never its content; cross-domain writers have no shared lock.
        await self._records.update(snapshot_id, {**row.data, "deleted": True}, row.revision)

    async def snapshot(self, workspace_id: str, lease: WorkspaceMutationLease) -> WorkspaceSnapshot:
        root = await self._workspaces.root_path(workspace_id)
        entries: list[WorkspaceArtifact] = []
        directories: list[str] = []
        for path in await lease.run(lambda: sorted(root.rglob("*"))):
            relative = path.relative_to(root).as_posix()
            is_directory, is_file, read_only = await lease.run(partial(inspect_entry, root, path))
            if is_directory:
                directories.append(relative)
            if is_file:
                data = await self._workspaces.read(workspace_id, relative)
                entries.append(
                    WorkspaceArtifact(
                        path=relative,
                        digest=await self._workspaces.put_blob(data),
                        size=len(data),
                        read_only=read_only,
                    )
                )
        result = WorkspaceSnapshot(
            id=uuid4().hex,
            workspace_id=workspace_id,
            entries=tuple(entries),
            directories=tuple(directories),
        )
        await lease.run(lambda: None)
        await self._records.create(
            result.id,
            result.model_dump(mode="json"),
            events=(
                WorkspaceSnapshotCreated(
                    payload={
                        "snapshot_id": result.id,
                        "workspace_id": workspace_id,
                    }
                ).model_dump(mode="json"),
            ),
        )
        return result

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot:
        record = await self._records.get(snapshot_id)
        if record is None or record.data.get("deleted"):
            raise OpsMeshError("NOT_FOUND", "WorkspaceSnapshot not found")
        return WorkspaceSnapshot.model_validate(record.data)
