"""Object-storage adapter for the SDK's native RemoteSnapshot extension."""

from __future__ import annotations

import io
from typing import BinaryIO, cast
from uuid import UUID

from temporalio import activity

from opsmesh.resources.storage.backends import ObjectStorage, run_storage_operation

SNAPSHOT_DEPENDENCY = "opsmesh.sandbox.snapshots"


class SandboxSnapshotStorage:
    """Store native session snapshots within the authorized workflow's tenant.

    Native session IDs and snapshots persist across Runs. Session ownership and
    current Run authorization are checked by SandboxAdmissionInterceptor before
    SDK Activities reach this adapter; the object key must not depend on run_id.
    The injected storage is borrowed and remains caller-owned.
    """

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    @staticmethod
    def _key(snapshot_id: str) -> str:
        identifier = UUID(snapshot_id)
        if str(identifier) != snapshot_id:
            raise ValueError("Native snapshot identity is invalid")
        workflow_id = activity.info().workflow_id
        if workflow_id is None:
            raise ValueError("Native snapshot requires an authorized workflow identity")
        parts = workflow_id.split(":")
        if len(parts) != 4 or parts[0] != "opsmesh" or parts[2] != "agent":
            raise ValueError("Native snapshot requires an authorized Agent workflow")
        workspace_id = UUID(parts[1])
        return f"workspaces/{workspace_id}/sandbox-snapshots/{identifier}/workspace.tar"

    async def upload(self, snapshot_id: str, data: io.IOBase) -> None:
        key = self._key(snapshot_id)
        await run_storage_operation(lambda: self._storage.write_stream(key, cast(BinaryIO, data)))

    async def download(self, snapshot_id: str) -> io.IOBase:
        key = self._key(snapshot_id)
        stream = await run_storage_operation(
            lambda: self._storage.open(key), on_cancelled_result=lambda result: result.close()
        )
        return cast(io.IOBase, stream)

    async def exists(self, snapshot_id: str) -> bool:
        key = self._key(snapshot_id)
        return await run_storage_operation(lambda: self._storage.exists(key))

    async def delete(self, snapshot_id: str) -> None:
        key = self._key(snapshot_id)
        await run_storage_operation(lambda: self._storage.delete(key))
