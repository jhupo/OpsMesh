"""Actor-aware workspace file and snapshot administration."""

from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import ResourceAuthorization

from .api import (
    ContentReferenceProvider,
    WorkspaceArtifact,
    WorkspaceAuthorization,
    WorkspaceDescriptor,
    WorkspaceFileInfo,
    WorkspaceManifest,
    WorkspaceService,
    WorkspaceSnapshot,
)
from .internal.locking import WorkspaceLockManager


class WorkspaceManagement:
    def __init__(
        self,
        workspaces: WorkspaceService,
        authorize: WorkspaceAuthorization,
        audit: AuditJournal,
        snapshot_references: ContentReferenceProvider,
        *,
        owners: VersionedRecordStore,
        authorize_creation: ResourceAuthorization,
    ) -> None:
        self._workspaces, self._authorize, self._audit = workspaces, authorize, audit
        self._snapshot_references = snapshot_references
        self._owners, self._authorize_creation = owners, authorize_creation
        self._allocation_locks = WorkspaceLockManager(owners.database, "workspace-allocation")

    async def create(self, workspace_id: str, *, actor_id: str) -> WorkspaceDescriptor:
        await self._authorize_creation(actor_id, "workspace:create", workspace_id)
        async with (
            self._allocation_locks.hold(workspace_id),
            audited_operation(self._audit, "workspace.create", workspace_id, actor_id),
        ):
            prior = await self._owners.get(workspace_id)
            if prior is None:
                await self._owners.create(workspace_id, {"owner_id": actor_id})
            elif prior.data["owner_id"] != actor_id:
                raise OpsMeshError("CONFLICT", "Workspace identifier is already allocated")
            elif prior.data.get("restore_operation_id"):
                try:
                    return await self._workspaces.get(workspace_id)
                except OpsMeshError as error:
                    if error.code not in {"NOT_FOUND", "WORKSPACE_RESTORING"}:
                        raise
                    raise OpsMeshError(
                        "CONFLICT", "Workspace identifier is allocated for restoration"
                    ) from error
            try:
                return await self._workspaces.create(workspace_id)
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
                return await self._workspaces.get(workspace_id)

    async def restore(
        self,
        snapshot_id: str,
        target_workspace_id: str,
        *,
        actor_id: str,
    ) -> WorkspaceDescriptor:
        await self.get_snapshot(snapshot_id, actor_id=actor_id)
        await self._authorize_creation(actor_id, "workspace:create", target_workspace_id)
        async with (
            self._allocation_locks.hold(target_workspace_id),
            audited_operation(self._audit, "workspace.restore", target_workspace_id, actor_id),
        ):
            prior = await self._owners.get(target_workspace_id)
            if prior is None:
                prior = await self._owners.create(
                    target_workspace_id,
                    {
                        "owner_id": actor_id,
                        "restore_snapshot_id": snapshot_id,
                        "restore_operation_id": uuid4().hex,
                        "restore_state": "pending",
                    },
                )
            elif (
                prior.data["owner_id"] != actor_id
                or prior.data.get("restore_snapshot_id") != snapshot_id
                or not prior.data.get("restore_operation_id")
            ):
                raise OpsMeshError("CONFLICT", "Workspace identifier is already allocated")
            restored = await self._workspaces.restore(
                snapshot_id,
                target_workspace_id,
                idempotency_key=str(prior.data["restore_operation_id"]),
            )
            if prior.data.get("restore_state") != "ready":
                await self._owners.update(
                    target_workspace_id, {**prior.data, "restore_state": "ready"}, prior.revision
                )
            return restored

    async def list(self, *, actor_id: str) -> tuple[WorkspaceDescriptor, ...]:
        result = []
        for workspace in await self._workspaces.list():
            try:
                await self._authorize(actor_id, workspace.id)
            except OpsMeshError as error:
                if error.code not in {"AUTHORIZATION_DENIED", "FORBIDDEN", "NOT_FOUND"}:
                    raise
            else:
                result.append(workspace)
        return tuple(result)

    async def get(self, workspace_id: str, *, actor_id: str) -> WorkspaceDescriptor:
        await self._authorize(actor_id, workspace_id)
        return await self._workspaces.get(workspace_id)

    async def tree(self, workspace_id: str, *, actor_id: str) -> tuple[WorkspaceFileInfo, ...]:
        await self._authorize(actor_id, workspace_id)
        return await self._workspaces.tree(workspace_id)

    async def stat(self, workspace_id: str, path: str, *, actor_id: str) -> WorkspaceFileInfo:
        await self._authorize(actor_id, workspace_id)
        return await self._workspaces.stat(workspace_id, path)

    async def read(self, workspace_id: str, path: str, *, actor_id: str) -> bytes:
        await self._authorize(actor_id, workspace_id)
        return await self._workspaces.read(workspace_id, path)

    async def write(
        self,
        workspace_id: str,
        path: str,
        data: bytes,
        *,
        actor_id: str,
        read_only: bool = False,
    ) -> WorkspaceArtifact:
        await self._authorize(actor_id, workspace_id, write=True)
        async with audited_operation(self._audit, "workspace.write", workspace_id, actor_id):
            return await self._workspaces.write(workspace_id, path, data, read_only=read_only)

    async def delete(self, workspace_id: str, *, actor_id: str) -> None:
        await self._authorize(actor_id, workspace_id, write=True)
        async with audited_operation(self._audit, "workspace.delete", workspace_id, actor_id):
            await self._workspaces.delete(workspace_id)

    async def replace_managed_file(
        self,
        workspace_id: str,
        path: str,
        data: bytes,
        *,
        actor_id: str,
        expected_digest: str | None,
        read_only: bool = True,
    ) -> WorkspaceArtifact:
        """Update trusted application files without granting agents that privilege.

        Ordinary workspace writers cannot replace read-only instructions. The
        allocation owner (or a delegated workspace manager) must also hold write
        access, and the content digest is checked inside the mutation lease.
        """
        await self._authorize(actor_id, workspace_id, write=True)
        owner = await self._owners.get(workspace_id)
        if owner is None or owner.data.get("owner_id") != actor_id:
            await self._authorize_creation(actor_id, "workspace:manage", workspace_id)
        async with audited_operation(
            self._audit,
            "workspace.replace_managed_file",
            workspace_id,
            actor_id,
            details={"path": path, "expected_digest": expected_digest},
        ):
            return await self._workspaces.replace_managed_file(
                workspace_id, path, data, expected_digest=expected_digest, read_only=read_only
            )

    async def delete_file(self, workspace_id: str, path: str, *, actor_id: str) -> None:
        await self._authorize(actor_id, workspace_id, write=True)
        async with audited_operation(self._audit, "workspace.delete_file", workspace_id, actor_id):
            await self._workspaces.delete_file(workspace_id, path)

    async def rename(
        self,
        workspace_id: str,
        source: str,
        target: str,
        *,
        actor_id: str,
    ) -> WorkspaceFileInfo:
        await self._authorize(actor_id, workspace_id, write=True)
        async with audited_operation(self._audit, "workspace.rename", workspace_id, actor_id):
            return await self._workspaces.rename(workspace_id, source, target)

    async def materialize(
        self,
        workspace_id: str,
        manifest: WorkspaceManifest,
        *,
        actor_id: str,
    ) -> tuple[WorkspaceArtifact, ...]:
        await self._authorize(actor_id, workspace_id, write=True)
        if any(entry.digest is not None for entry in manifest.entries):
            raise OpsMeshError(
                "AUTHORIZATION_DENIED", "Use authorized snapshots to access stored content"
            )
        async with audited_operation(self._audit, "workspace.materialize", workspace_id, actor_id):
            return await self._workspaces.materialize(workspace_id, manifest)

    async def snapshot(self, workspace_id: str, *, actor_id: str) -> WorkspaceSnapshot:
        await self._authorize(actor_id, workspace_id)
        async with audited_operation(self._audit, "workspace.snapshot", workspace_id, actor_id):
            return await self._workspaces.snapshot(workspace_id)

    async def list_snapshots(
        self, workspace_id: str, *, actor_id: str
    ) -> tuple[WorkspaceSnapshot, ...]:
        await self._authorize(actor_id, workspace_id)
        return await self._workspaces.list_snapshots(workspace_id)

    async def get_snapshot(self, snapshot_id: str, *, actor_id: str) -> WorkspaceSnapshot:
        snapshot = await self._workspaces.get_snapshot(snapshot_id)
        await self._authorize(actor_id, snapshot.workspace_id)
        return snapshot

    async def delete_snapshot(self, snapshot_id: str, *, actor_id: str) -> None:
        snapshot = await self.get_snapshot(snapshot_id, actor_id=actor_id)
        await self._authorize(actor_id, snapshot.workspace_id, write=True)
        async with audited_operation(
            self._audit, "workspace.delete_snapshot", snapshot_id, actor_id
        ):
            await self._workspaces.delete_snapshot(snapshot_id, self._snapshot_references)
