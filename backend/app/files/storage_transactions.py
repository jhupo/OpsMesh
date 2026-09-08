from __future__ import annotations

from backend.app.files.storage import ObjectStorage


class ObjectStorageKeyConflictError(ValueError):
    pass


class ObjectStorageCompensationError(RuntimeError):
    pass


class CompensatingObjectStorageWrites:
    """Tracks newly-created objects so a failed database transaction can remove them."""

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage
        self._created_keys: list[str] = []
        self._completed = False

    def write_new(self, storage_key: str, content: bytes) -> None:
        if self._completed:
            raise RuntimeError("Object storage write batch is already completed")
        if self._storage.exists(storage_key):
            raise ObjectStorageKeyConflictError("Object storage key already exists")
        try:
            self._storage.write(storage_key, content)
        except Exception:
            self._remove_partial_write(storage_key)
            raise
        self._created_keys.append(storage_key)

    def complete(self) -> None:
        self._created_keys.clear()
        self._completed = True

    def compensate(self) -> None:
        if self._completed:
            return
        failed_keys: list[str] = []
        for storage_key in reversed(self._created_keys):
            try:
                self._storage.delete(storage_key)
            except Exception:
                failed_keys.append(storage_key)
        self._created_keys = list(reversed(failed_keys))
        if failed_keys:
            raise ObjectStorageCompensationError(
                f"Failed to remove {len(failed_keys)} object storage write(s)"
            )

    def _remove_partial_write(self, storage_key: str) -> None:
        try:
            self._storage.delete(storage_key)
        except Exception as exc:
            raise ObjectStorageCompensationError(
                "Failed to remove a partial object storage write"
            ) from exc
