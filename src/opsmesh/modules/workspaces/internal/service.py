"""Filesystem workspaces with independently pluggable immutable blob storage."""

import hashlib
import os
import shutil
import stat
from functools import partial
from pathlib import Path

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.storage import BlobStore, LocalBlobStore
from opsmesh.modules.workspaces.api import (
    ContentReferenceProvider,
    WorkspaceArtifact,
    WorkspaceDescriptor,
    WorkspaceFileInfo,
    WorkspaceGarbageCollection,
    WorkspaceManifest,
    WorkspaceSnapshot,
)

from .files import file_info, list_tree, remove_file, rename_entry
from .garbage_collection import collect_content
from .locking import WorkspaceLockManager
from .paths import atomic_write, checked_id, checked_path
from .restoration import WorkspaceRestoration
from .snapshots import WorkspaceSnapshots


class LocalWorkspaceService:
    def __init__(
        self,
        database: AsyncDatabase,
        root: Path,
        *,
        max_file_bytes: int = 64 << 20,
        mutation_lease_seconds: float = 30,
        mutation_wait_seconds: float = 30,
        blob_store: BlobStore | None = None,
    ) -> None:
        if max_file_bytes <= 0:
            raise OpsMeshError("CONFIG_INVALID", "Workspace file size limit must be positive")
        self._workspaces = VersionedRecordStore(database, "workspaces")
        self._snapshots = WorkspaceSnapshots(database, self)
        self._root = root.resolve()
        self._locks = WorkspaceLockManager(
            database,
            os.path.normcase(str(self._root)),
            lease_seconds=mutation_lease_seconds,
            wait_seconds=mutation_wait_seconds,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        self._files = self._root / "workspaces"
        self._files.mkdir(exist_ok=True)
        self._limit = max_file_bytes
        self._content = blob_store or LocalBlobStore(
            self._root / "objects", max_blob_bytes=max_file_bytes
        )
        self._content_locks = WorkspaceLockManager(
            database,
            f"blob:{self._content.identity}",
            lease_seconds=mutation_lease_seconds,
            wait_seconds=mutation_wait_seconds,
        )
        self._restoration = WorkspaceRestoration(
            self._workspaces,
            self._files,
            self._root / "restore-staging",
            lambda digest: self.get_blob(digest),
        )

    async def create(self, workspace_id: str) -> WorkspaceDescriptor:
        checked_id(workspace_id)
        path = self._files / workspace_id

        def create_directory() -> None:
            if path.exists():
                raise OpsMeshError("CONFLICT", "Workspace directory already exists")
            path.mkdir()

        async with self._locks.hold(workspace_id) as lease:
            if await self._workspaces.get(workspace_id) is not None:
                raise OpsMeshError("CONFLICT", "Workspace identifier is already allocated")
            await lease.run(create_directory)
            try:
                await self._workspaces.create(workspace_id, {})
            except BaseException:
                await lease.run(path.rmdir)
                raise
        return WorkspaceDescriptor(id=workspace_id)

    async def get(self, workspace_id: str) -> WorkspaceDescriptor:
        checked_id(workspace_id)
        record = await self._workspaces.get(workspace_id)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "WorkspaceDescriptor not found")
        if record.data.get("restore_state") == "staging":
            raise OpsMeshError("WORKSPACE_RESTORING", "Workspace restoration must complete first")
        return WorkspaceDescriptor(id=workspace_id, revision=record.revision)

    async def list(self) -> tuple[WorkspaceDescriptor, ...]:
        return tuple(
            WorkspaceDescriptor(id=record.id, revision=record.revision)
            for record in await self._workspaces.list()
            if record.data.get("restore_state") != "staging"
        )

    async def root_path(self, workspace_id: str) -> Path:
        await self.get(workspace_id)
        path = self._files / workspace_id
        if (
            path.is_symlink()
            or not path.is_dir()
            or not path.resolve().is_relative_to(self._files.resolve())
        ):
            raise OpsMeshError("WORKSPACE_UNAVAILABLE", "WorkspaceDescriptor root is unavailable")
        return path

    async def put_blob(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        if len(data) > self._limit:
            raise OpsMeshError("RESOURCE_EXHAUSTED", "Workspace blob exceeds size limit")
        async with self._content_locks.hold(digest) as lease:
            return await lease.run_async(partial(self._content.put, data), mark_content_use=True)

    async def get_blob(self, digest: str) -> bytes:
        async with self._content_locks.hold(digest) as lease:
            data = await lease.run_async(partial(self._content.get, digest))
            if len(data) > self._limit:
                raise OpsMeshError("RESOURCE_EXHAUSTED", "Workspace blob exceeds size limit")
            return data

    async def close(self) -> None:
        await self._content.close()

    @property
    def blob_store(self) -> BlobStore:
        return self._content

    async def collect_garbage(
        self,
        reference_provider: ContentReferenceProvider,
        *,
        minimum_age_seconds: float = 3600,
        dry_run: bool = True,
        maintenance: bool = False,
    ) -> WorkspaceGarbageCollection:
        return await collect_content(
            self._content,
            self._content_locks,
            reference_provider,
            minimum_age_seconds=minimum_age_seconds,
            dry_run=dry_run,
            maintenance=maintenance,
        )

    async def write(
        self, workspace_id: str, path: str, data: bytes, *, read_only: bool = False
    ) -> WorkspaceArtifact:
        async with self._locks.hold(workspace_id) as lease:
            root = await self.root_path(workspace_id)
            digest = await self.put_blob(data)
            await lease.run(
                lambda: atomic_write(checked_path(root, path), data, read_only=read_only)
            )
            return WorkspaceArtifact(path=path, digest=digest, size=len(data), read_only=read_only)

    async def read(self, workspace_id: str, path: str) -> bytes:
        async with self._locks.hold(workspace_id) as lease:
            root = await self.root_path(workspace_id)

            def read_file() -> bytes:
                destination = checked_path(root, path)
                if not destination.is_file():
                    raise OpsMeshError("NOT_FOUND", "Workspace file not found")
                if destination.stat().st_size > self._limit:
                    raise OpsMeshError("RESOURCE_EXHAUSTED", "File exceeds size limit")
                return destination.read_bytes()

            return await lease.run(read_file)

    async def tree(self, workspace_id: str) -> tuple[WorkspaceFileInfo, ...]:
        async with self._locks.hold(workspace_id) as lease:
            root = await self.root_path(workspace_id)
            return await lease.run(partial(list_tree, root))

    async def stat(self, workspace_id: str, path: str) -> WorkspaceFileInfo:
        async with self._locks.hold(workspace_id) as lease:
            root = await self.root_path(workspace_id)
            return await lease.run(partial(file_info, root, path))

    async def delete_file(self, workspace_id: str, path: str) -> None:
        async with self._locks.hold(workspace_id) as lease:
            root = await self.root_path(workspace_id)
            await lease.run(partial(remove_file, root, path))

    async def rename(self, workspace_id: str, source: str, target: str) -> WorkspaceFileInfo:
        async with self._locks.hold(workspace_id) as lease:
            root = await self.root_path(workspace_id)
            return await lease.run(partial(rename_entry, root, source, target))

    async def materialize(
        self, workspace_id: str, manifest: WorkspaceManifest
    ) -> tuple[WorkspaceArtifact, ...]:
        async with self._locks.hold(workspace_id):
            return await self._materialize(workspace_id, manifest)

    async def _materialize(
        self, workspace_id: str, manifest: WorkspaceManifest
    ) -> tuple[WorkspaceArtifact, ...]:
        root = await self.root_path(workspace_id)
        pending: list[tuple[str, bytes, bool]] = []
        seen: set[str] = set()
        directories: list[Path] = []
        for directory in manifest.directories:
            target = checked_path(root, directory)
            if target.exists() and not target.is_dir():
                raise OpsMeshError("CONFLICT", "Manifest directory is occupied by a file")
            directories.append(target)
        for entry in manifest.entries:
            target = checked_path(root, entry.path)
            if entry.path in seen or target.exists():
                raise OpsMeshError("CONFLICT", "Materialization cannot overwrite existing files")
            if (entry.content is None) == (entry.digest is None):
                raise OpsMeshError("CONFIG_INVALID", "Entry requires exactly content or digest")
            seen.add(entry.path)
            data = (
                entry.content.encode()
                if entry.content is not None
                else await self.get_blob(entry.digest or "")
            )
            if len(data) > self._limit:
                raise OpsMeshError("RESOURCE_EXHAUSTED", "File exceeds size limit")
            pending.append((entry.path, data, entry.read_only))
        for first in seen:
            if any(
                other == first or other.startswith(first + "/") for other in manifest.directories
            ) or any(other.startswith(first + "/") for other in seen):
                raise OpsMeshError("CONFIG_INVALID", "Manifest file paths overlap")
        async with self._locks.hold(workspace_id) as lease:
            for directory_path in directories:
                await lease.run(partial(directory_path.mkdir, parents=True, exist_ok=True))
        return tuple(
            [
                await self.write(workspace_id, path, data, read_only=read_only)
                for path, data, read_only in pending
            ]
        )

    async def snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        async with self._locks.hold(workspace_id) as lease:
            return await self._snapshots.snapshot(workspace_id, lease)

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot:
        return await self._snapshots.get_snapshot(snapshot_id)

    async def list_snapshots(self, workspace_id: str) -> tuple[WorkspaceSnapshot, ...]:
        return await self._snapshots.list(workspace_id)

    async def delete_snapshot(
        self,
        snapshot_id: str,
        reference_provider: ContentReferenceProvider,
    ) -> None:
        snapshot = await self.get_snapshot(snapshot_id)
        async with self._locks.hold(snapshot.workspace_id):
            await self._snapshots.delete(snapshot_id, reference_provider)

    async def restore(
        self,
        snapshot_id: str,
        target_workspace_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> WorkspaceDescriptor:
        snapshot = await self.get_snapshot(snapshot_id)
        async with self._locks.hold(target_workspace_id) as lease:
            return await self._restoration.restore(
                snapshot, target_workspace_id, lease, idempotency_key=idempotency_key
            )

    async def delete(self, workspace_id: str) -> None:
        async with self._locks.hold(workspace_id) as lease:
            workspace = await self.get(workspace_id)
            root = await self.root_path(workspace_id)

            def remove_directory() -> None:
                # Validated workspace only; external direct filesystem writers are unsupported.
                for path in root.rglob("*"):
                    checked_path(root, path.relative_to(root).as_posix())
                    if path.is_file() and not path.is_symlink():
                        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
                shutil.rmtree(root)

            await lease.run(remove_directory)
            await self._workspaces.delete(workspace_id, expected_revision=workspace.revision)
