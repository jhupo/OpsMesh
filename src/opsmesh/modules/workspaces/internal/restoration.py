"""Recoverable workspace publication from private staging directories."""

import hashlib
import shutil
import stat
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.modules.workspaces.api import WorkspaceDescriptor, WorkspaceSnapshot

from .locking import WorkspaceMutationLease
from .paths import atomic_write, checked_id, checked_path


class WorkspaceRestoration:
    def __init__(
        self,
        records: VersionedRecordStore,
        files: Path,
        staging: Path,
        read_blob: Callable[[str], Awaitable[bytes]],
    ) -> None:
        self.records, self.files, self.staging, self.read_blob = records, files, staging, read_blob
        self.staging.mkdir(exist_ok=True)

    async def restore(
        self,
        snapshot: WorkspaceSnapshot,
        target_id: str,
        lease: WorkspaceMutationLease,
        *,
        idempotency_key: str | None,
    ) -> WorkspaceDescriptor:
        checked_id(target_id)
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 200:
            raise OpsMeshError(
                "CONFIG_INVALID", "Restore identity must contain 1 to 200 characters"
            )
        operation_id = idempotency_key or uuid4().hex
        record = await self.records.get(target_id)
        if (
            idempotency_key is None
            and record is not None
            and record.data.get("restore_state") == "staging"
            and record.data.get("restore_snapshot_id") == snapshot.id
            and isinstance(record.data.get("restore_operation_id"), str)
        ):
            # The caller holds the target fence: an unfinished anonymous restore is
            # recoverable, while a completed target still conflicts without a key.
            operation_id = record.data["restore_operation_id"]
        target = self.files / target_id
        if record is None:
            await lease.run(lambda: self._require_absent(target))
            record = await self.records.create(
                target_id,
                {
                    "restore_snapshot_id": snapshot.id,
                    "restore_operation_id": operation_id,
                    "restore_state": "staging",
                },
            )
        elif (
            record.data.get("restore_snapshot_id") != snapshot.id
            or record.data.get("restore_operation_id") != operation_id
        ):
            raise OpsMeshError(
                "CONFLICT", "Workspace already exists or belongs to another operation"
            )
        if record.data.get("restore_state") == "ready":
            if not await lease.run(lambda: self._published(target)):
                raise OpsMeshError(
                    "WORKSPACE_UNAVAILABLE", "Published restore directory is missing"
                )
            return WorkspaceDescriptor(id=target_id, revision=record.revision)

        # A process may have died after atomic rename and before recording completion.
        # Only this operation can have published the target while the row is staging.
        published = await lease.run(lambda: self._published(target))
        if not published:
            identity = hashlib.sha256(f"{target_id}:{operation_id}".encode()).hexdigest()
            stage = self.staging / identity
            await lease.run(lambda: self._prepare(stage))
            for directory in snapshot.directories:
                await lease.run(
                    partial(checked_path(stage, directory).mkdir, parents=True, exist_ok=True)
                )
            for entry in snapshot.entries:
                content = await self.read_blob(entry.digest)
                await lease.run(
                    partial(
                        atomic_write,
                        checked_path(stage, entry.path),
                        content,
                        read_only=entry.read_only,
                    )
                )
            await lease.run(lambda: self._publish(stage, target))
        saved = await self.records.update(
            target_id, {**record.data, "restore_state": "ready"}, record.revision
        )
        return WorkspaceDescriptor(id=target_id, revision=saved.revision)

    @staticmethod
    def _require_absent(target: Path) -> None:
        if target.exists() or target.is_symlink():
            raise OpsMeshError("CONFLICT", "Workspace directory already exists")

    def _published(self, target: Path) -> bool:
        checked_path(self.files, target.name)
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise OpsMeshError("WORKSPACE_UNAVAILABLE", "Restore target is not a safe directory")
        return target.is_dir()

    def _prepare(self, stage: Path) -> None:
        checked_path(self.staging, stage.name)
        if stage.exists():
            for path in stage.rglob("*"):
                checked_path(stage, path.relative_to(stage).as_posix())
                if path.is_file():
                    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            shutil.rmtree(stage)
        stage.mkdir()

    def _publish(self, stage: Path, target: Path) -> None:
        self._require_absent(target)
        stage.rename(target)
