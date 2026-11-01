"""Versioned workspace content and safe materialization contracts."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, Protocol

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.infrastructure.storage import BlobStore


class WorkspaceDescriptor(OpsMeshContract):
    id: str
    revision: int = 1


class WorkspaceArtifact(OpsMeshContract):
    path: str
    digest: str
    size: int
    read_only: bool = False


class WorkspaceFileInfo(OpsMeshContract):
    path: str
    kind: Literal["file", "directory"]
    size: int = 0
    read_only: bool = False


class WorkspaceEntry(OpsMeshContract):
    path: str
    content: str | None = None
    digest: str | None = None
    read_only: bool = False


class WorkspaceManifest(OpsMeshContract):
    entries: tuple[WorkspaceEntry, ...] = ()
    directories: tuple[str, ...] = ()


class WorkspaceSnapshot(OpsMeshContract):
    id: str
    workspace_id: str
    entries: tuple[WorkspaceArtifact, ...]
    directories: tuple[str, ...] = ()


class WorkspaceGarbageCollection(OpsMeshContract):
    dry_run: bool
    candidates: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    reclaimed_bytes: int = 0


ContentReferenceProvider = Callable[[], Awaitable[set[str]]]


class WorkspaceAuthorization(Protocol):
    async def __call__(
        self, principal_id: str, workspace_id: str, *, write: bool = False
    ) -> None: ...


class WorkspaceService(Protocol):
    async def tree(self, workspace_id: str) -> tuple[WorkspaceFileInfo, ...]: ...
    async def stat(self, workspace_id: str, path: str) -> WorkspaceFileInfo: ...
    async def delete_file(self, workspace_id: str, path: str) -> None: ...
    async def rename(self, workspace_id: str, source: str, target: str) -> WorkspaceFileInfo: ...
    async def list_snapshots(self, workspace_id: str) -> tuple[WorkspaceSnapshot, ...]: ...
    async def delete_snapshot(
        self,
        snapshot_id: str,
        reference_provider: ContentReferenceProvider,
    ) -> None: ...
    @property
    def blob_store(self) -> BlobStore:
        """Maintenance access; callers must enforce reference and stop-writer policies."""
        ...

    async def close(self) -> None: ...

    async def create(self, workspace_id: str) -> WorkspaceDescriptor: ...
    async def list(self) -> tuple[WorkspaceDescriptor, ...]: ...
    async def get(self, workspace_id: str) -> WorkspaceDescriptor: ...
    async def root_path(self, workspace_id: str) -> Path: ...
    async def write(
        self, workspace_id: str, path: str, data: bytes, *, read_only: bool = False
    ) -> WorkspaceArtifact: ...
    async def read(self, workspace_id: str, path: str) -> bytes: ...
    async def put_blob(self, data: bytes) -> str: ...
    async def get_blob(self, digest: str) -> bytes: ...
    async def materialize(
        self, workspace_id: str, manifest: WorkspaceManifest
    ) -> tuple[WorkspaceArtifact, ...]: ...
    async def snapshot(self, workspace_id: str) -> WorkspaceSnapshot: ...
    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot: ...
    async def restore(
        self,
        snapshot_id: str,
        target_workspace_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> WorkspaceDescriptor: ...
    async def delete(self, workspace_id: str) -> None: ...
    async def collect_garbage(
        self,
        reference_provider: ContentReferenceProvider,
        *,
        minimum_age_seconds: float = 3600,
        dry_run: bool = True,
        maintenance: bool = False,
    ) -> WorkspaceGarbageCollection: ...
