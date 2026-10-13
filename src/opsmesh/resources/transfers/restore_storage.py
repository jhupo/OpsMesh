"""Record every disposable object locator before issuing its external write."""

from typing import BinaryIO
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.resources.transfers.models import WorkspaceRestoreDrillOperation
from opsmesh.resources.transfers.restore_operations import validate_drill_storage_key


class RestoreDrillStorage:
    """Track temporary drill objects while borrowing the caller's storage and engine."""

    def __init__(
        self, engine: Engine, storage: ObjectStorage, workspace_id: UUID, operation_id: UUID
    ) -> None:
        self._engine, self._storage = engine, storage
        self._workspace_id, self._operation_id = workspace_id, operation_id

    def close(self) -> None:
        """Leave borrowed storage and engine open; drill cleanup remains explicit."""

    def _record(self, key: str) -> None:
        with Session(self._engine) as session:
            operation = session.scalar(
                select(WorkspaceRestoreDrillOperation)
                .where(
                    WorkspaceRestoreDrillOperation.workspace_id == self._workspace_id,
                    WorkspaceRestoreDrillOperation.id == self._operation_id,
                    WorkspaceRestoreDrillOperation.status == "running",
                )
                .with_for_update()
            )
            if operation is None:
                raise ValueError("Restore drill operation is no longer running")
            validate_drill_storage_key(key, operation.temporary_workspace_id)
            operation.storage_keys = list(dict.fromkeys([*operation.storage_keys, key]))
            session.commit()

    def cleanup(self) -> None:
        with Session(self._engine) as session:
            operation = session.scalar(
                select(WorkspaceRestoreDrillOperation).where(
                    WorkspaceRestoreDrillOperation.workspace_id == self._workspace_id,
                    WorkspaceRestoreDrillOperation.id == self._operation_id,
                )
            )
            if operation is None:
                raise ValueError("Restore drill receipt is unavailable")
            keys, temporary_id = list(operation.storage_keys), operation.temporary_workspace_id
        for key in keys:
            validate_drill_storage_key(key, temporary_id)
            self._storage.delete(key)

    def write(self, storage_key: str, content: bytes) -> None:
        self._record(storage_key)
        self._storage.write(storage_key, content)

    def write_stream(self, storage_key: str, content: BinaryIO) -> None:
        self._record(storage_key)
        self._storage.write_stream(storage_key, content)

    def put(self, storage_key: str, content: bytes) -> None:
        self.write(storage_key, content)

    def get(self, storage_key: str) -> bytes:
        return self._storage.get(storage_key)

    def read(self, storage_key: str) -> bytes:
        return self._storage.read(storage_key)

    def read_limited(self, storage_key: str, max_bytes: int) -> bytes:
        return self._storage.read_limited(storage_key, max_bytes)

    def open(self, storage_key: str) -> BinaryIO:
        return self._storage.open(storage_key)

    def exists(self, storage_key: str) -> bool:
        return self._storage.exists(storage_key)

    def delete(self, storage_key: str) -> None:
        with Session(self._engine) as session:
            operation = session.scalar(
                select(WorkspaceRestoreDrillOperation).where(
                    WorkspaceRestoreDrillOperation.workspace_id == self._workspace_id,
                    WorkspaceRestoreDrillOperation.id == self._operation_id,
                )
            )
            if operation is None:
                raise ValueError("Restore drill receipt is unavailable")
            validate_drill_storage_key(storage_key, operation.temporary_workspace_id)
        self._storage.delete(storage_key)
